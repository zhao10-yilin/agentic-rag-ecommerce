"""Business KPI attribution — from Agent conversation to downstream conversion.

The core challenge: an Agent recommends a product, and 3 days later the user
buys it. How do we attribute that purchase to THIS conversation and not the
Google ad they clicked yesterday?

Design decisions:
1. **Multi-level success definition** — click / add-to-cart / purchase, not binary.
2. **Trace-level attribution** — each AgentResponse carries a trace_id that flows
   into recommended URLs, surfaces in checkout events.
3. **Attribution window** — 7-day lookback, last-touch model by default.
4. **A/B test unit** — user_id hashed into experiment buckets (not session_id),
   because the same user across sessions must stay in one group.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Success levels
# ---------------------------------------------------------------------------


class ConversionLevel(str, Enum):
    """Escalating levels of recommendation success.

    Not every conversation is expected to reach PURCHASE — a "show me coffee
    machines" query that ends with a click is already successful.
    """

    IMPRESSION = "impression"      # Answer was shown to user
    CLICK = "click"                # User clicked a recommended product link
    ADD_TO_CART = "add_to_cart"    # User added recommended product to cart
    PURCHASE = "purchase"          # User completed purchase of recommended product
    RETURNED = "returned"          # User returned the product (negative signal)


@dataclass
class AttributionEvent:
    """A downstream event linked to an Agent conversation trace."""

    trace_id: str
    user_id: str
    session_id: str | None
    experiment_group: str
    product_id: str
    level: ConversionLevel
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Attribution Logger (write side)
# ---------------------------------------------------------------------------


class AttributionLogger:
    """Records attribution events to a structured log or database.

    In production this writes to a ClickHouse / BigQuery events table.
    For development it writes JSON lines to a file.
    """

    def __init__(self, log_path: str = "data/attribution_events.jsonl") -> None:
        import os
        os.makedirs("data", exist_ok=True)
        self._path = log_path

    def log(self, event: AttributionEvent) -> None:
        line = json.dumps({
            "trace_id": event.trace_id,
            "user_id": event.user_id,
            "session_id": event.session_id,
            "experiment_group": event.experiment_group,
            "product_id": event.product_id,
            "level": event.level.value,
            "timestamp": datetime.fromtimestamp(event.timestamp, tz=timezone.utc).isoformat(),
            "metadata": event.metadata,
        }, ensure_ascii=False)

        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# Attribution Window (read side — batch/offline)
# ---------------------------------------------------------------------------


class AttributionWindow:
    """Joins purchase events back to Agent conversations.

    Key parameters
    --------------
    window_days:
        Lookback from purchase to conversation. Default 7 days.
    model:
        Attribution model — currently only 'last_touch'.
    """

    def __init__(
        self,
        *,
        window_days: int = 7,
        model: str = "last_touch",
    ) -> None:
        self._window = timedelta(days=window_days)
        self._model = model

    def attribute(
        self,
        purchase: dict[str, Any],       # {user_id, product_id, purchase_time, order_id}
        conversations: list[dict[str, Any]],  # List of {trace_id, user_id, session_id, experiment_group, recommended_product_ids, time}
    ) -> AttributionEvent | None:
        """Given a purchase, find the most recent matching Agent conversation.

        Returns None if no conversation within the window recommended this product.
        """
        purchase_time = datetime.fromtimestamp(purchase["purchase_time"], tz=timezone.utc)
        user_id = purchase["user_id"]
        product_id = purchase["product_id"]

        # Filter: same user, within window, recommended this product
        candidates = []
        for conv in conversations:
            conv_time = datetime.fromtimestamp(conv["time"], tz=timezone.utc)
            if conv_time > purchase_time:
                continue  # Conversation happened AFTER purchase — can't attribute
            if purchase_time - conv_time > self._window:
                continue  # Outside attribution window
            if conv["user_id"] != user_id:
                continue  # Different user
            if product_id not in conv.get("recommended_product_ids", []):
                continue  # This conversation didn't recommend the purchased product

            candidates.append((purchase_time - conv_time, conv))

        if not candidates:
            return None  # Organic purchase, or attributed to another channel

        # Last-touch: pick the most recent conversation
        candidates.sort(key=lambda x: x[0])  # Sort by time delta ascending
        _, best_conv = candidates[0]

        return AttributionEvent(
            trace_id=best_conv["trace_id"],
            user_id=user_id,
            session_id=best_conv.get("session_id"),
            experiment_group=best_conv.get("experiment_group", "control"),
            product_id=product_id,
            level=ConversionLevel.PURCHASE,
            metadata={
                "order_id": purchase.get("order_id", ""),
                "attribution_model": self._model,
                "window_days": self._window.days,
                "hours_since_conversation": candidates[0][0].total_seconds() / 3600,
            },
        )


# ---------------------------------------------------------------------------
# A/B Test Bucketing
# ---------------------------------------------------------------------------


class ExperimentBucketer:
    """Deterministic experiment group assignment.

    Randomization unit: **user_id**, hashed with experiment_name as salt.
    This ensures:
    - Same user always gets the same variant across sessions.
    - Adding a new variant doesn't reshuffle existing users.
    - No session-level contamination (user sees variant A on mobile, B on web).
    """

    def __init__(self, experiment_name: str, groups: list[str]) -> None:
        if len(groups) < 2:
            raise ValueError("Need at least 2 experiment groups")
        self._experiment = experiment_name
        self._groups = groups

    def assign(self, user_id: str) -> str:
        """Deterministic hash-based assignment.

        Uses SHA-256(experiment_name + user_id) → first 8 hex chars → mod group count.
        """
        seed = f"{self._experiment}:{user_id}"
        hash_hex = hashlib.sha256(seed.encode()).hexdigest()[:8]
        bucket = int(hash_hex, 16) % len(self._groups)
        return self._groups[bucket]

    def get_group_distribution(self, user_ids: list[str]) -> dict[str, int]:
        """Check group balance for a list of users."""
        counts: dict[str, int] = {g: 0 for g in self._groups}
        for uid in user_ids:
            counts[self.assign(uid)] += 1
        return counts


# ---------------------------------------------------------------------------
# Metrics Aggregator
# ---------------------------------------------------------------------------


class AttributionMetrics:
    """Computes business KPIs from attribution events.

    Key metrics:
    - click_through_rate: clicks / impressions
    - add_to_cart_rate: add_to_carts / clicks
    - conversion_rate: purchases / impressions
    - return_rate: returns / purchases
    - time_to_purchase_hours: avg hours from conversation to purchase
    """

    @staticmethod
    def compute(
        events: list[AttributionEvent],
        *,
        group_by: str = "experiment_group",
    ) -> dict[str, dict[str, float]]:
        """Compute per-group metrics from a list of attribution events.

        Returns {group_name: {metric: value}}.
        """
        groups: dict[str, dict[str, int]] = {}

        for e in events:
            group = getattr(e, group_by, "unknown")
            if group not in groups:
                groups[group] = {
                    "impressions": 0, "clicks": 0, "add_to_carts": 0,
                    "purchases": 0, "returns": 0,
                    "total_time_to_purchase_hours": 0, "purchase_count_for_time": 0,
                }
            g = groups[group]

            if e.level == ConversionLevel.IMPRESSION:
                g["impressions"] += 1
            elif e.level == ConversionLevel.CLICK:
                g["clicks"] += 1
            elif e.level == ConversionLevel.ADD_TO_CART:
                g["add_to_carts"] += 1
            elif e.level == ConversionLevel.PURCHASE:
                g["purchases"] += 1
                # Track time-to-purchase
                hours = e.metadata.get("hours_since_conversation")
                if hours is not None:
                    g["total_time_to_purchase_hours"] += hours
                    g["purchase_count_for_time"] += 1
            elif e.level == ConversionLevel.RETURNED:
                g["returns"] += 1

        metrics = {}
        for group, counts in groups.items():
            imp = max(counts["impressions"], 1)
            clk = max(counts["clicks"], 1)
            pur = max(counts["purchases"], 1)
            metrics[group] = {
                "click_through_rate": round(counts["clicks"] / imp, 4),
                "add_to_cart_rate": round(counts["add_to_carts"] / clk, 4),
                "conversion_rate": round(counts["purchases"] / imp, 4),
                "return_rate": round(counts["returns"] / pur, 4),
                "avg_time_to_purchase_hours": round(
                    counts["total_time_to_purchase_hours"] / max(counts["purchase_count_for_time"], 1), 1
                ),
                "total_impressions": counts["impressions"],
                "total_purchases": counts["purchases"],
            }
        return metrics
