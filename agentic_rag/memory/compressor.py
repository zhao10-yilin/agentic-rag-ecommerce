"""Tiered memory loading — prevents context stuffing in Planner prompts.

Problem: If we dump full user profiles (30+ preference tags, 20+ purchase
items, 5+ past conversations) into every Planner call, the LLM suffers from
"lost-in-the-middle" — critical tool descriptions and planning rules get
pushed to lower-attention regions of the context window.

Solution: Three-tier loading keyed by query relevance.

Tier 1 — Identity signals (~100 tokens, always loaded):
    price_range, skill_level, primary_category.  These are essential for
    ANY e-commerce query regardless of topic.

Tier 2 — Query-relevant history (~200 tokens, loaded by keyword overlap):
    Purchase history filtered to same category as query.  Past conversations
    whose intent matches current intent.  Key preferences that overlap with
    query keywords.

Tier 3 — Full context (unlimited, loaded on explicit request):
    Full purchase history, all conversations, complete preference tree.
    Only loaded when Tier 2 was insufficient (e.g., "compare with what I
    bought last year").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------


@dataclass
class TieredMemory:
    """Memory partitioned by loading priority."""

    tier1_summary: str = ""         # Always loaded (~100 tokens)
    tier2_context: str = ""         # Loaded if relevant (~200 tokens)
    tier3_full: dict[str, Any] = field(default_factory=dict)  # On demand

    def to_prompt_block(self, *, include_tier2: bool = True) -> str:
        """Build the memory block for the Planner prompt.

        Tier 1 is always included.  Tier 2 is included only if *include_tier2*
        is True AND the tier is non-empty (i.e., the query matched something).
        """
        parts = [self.tier1_summary]
        if include_tier2 and self.tier2_context:
            parts.append(self.tier2_context)
        return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Compressor
# ---------------------------------------------------------------------------


class MemoryCompressor:
    """Compresses raw memory into tiered blocks.

    The compression is query-aware: it filters purchase history and past
    conversations to only include items relevant to the current query.

    Parameters
    ----------
    max_tier1_tokens:
        Approximate token budget for Tier 1 (always-loaded identity).
    max_tier2_tokens:
        Approximate token budget for Tier 2 (query-relevant context).
    """

    def __init__(
        self,
        *,
        max_tier1_tokens: int = 150,
        max_tier2_tokens: int = 300,
    ) -> None:
        self._t1_budget = max_tier1_tokens
        self._t2_budget = max_tier2_tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compress(
        self,
        user_profile: dict[str, Any] | None,
        purchase_history: list[dict[str, Any]] | None,
        past_conversations: list[dict[str, Any]] | None,
        *,
        current_query: str = "",
        current_intent: str = "",
    ) -> TieredMemory:
        """Compress raw memory into tiered blocks.

        Parameters
        ----------
        user_profile:
            Full profile dict with preferences, demographics, etc.
        purchase_history:
            List of {date, product, price, category, ...} dicts.
        past_conversations:
            List of {query, answer, intent, timestamp, ...} dicts.
        current_query:
            The user's current query, used for relevance filtering.
        current_intent:
            The classified intent, used to match past conversations.
        """
        result = TieredMemory()
        profile = user_profile or {}
        purchases = purchase_history or []
        conversations = past_conversations or []

        # ---- Tier 1: Identity signals ----
        result.tier1_summary = self._build_tier1(profile)

        # ---- Tier 2: Query-relevant context ----
        if current_query:
            result.tier2_context = self._build_tier2(
                profile, purchases, conversations,
                query=current_query, intent=current_intent,
            )

        # ---- Tier 3: Full context (kept in structured form) ----
        result.tier3_full = {
            "profile": profile,
            "purchases": purchases,
            "conversations": conversations,
        }

        return result

    # ------------------------------------------------------------------
    # Tier builders
    # ------------------------------------------------------------------

    def _build_tier1(self, profile: dict[str, Any]) -> str:
        """Extract identity-critical signals only."""
        prefs = profile.get("preferences", {})

        signals: list[str] = []

        # Price range — essential for ALL recommendations
        price = prefs.get("price_range") or prefs.get("budget")
        if price:
            signals.append(f"价格偏好: {price}")

        # Skill level — determines product complexity
        skill = prefs.get("skill_level")
        if skill:
            signals.append(f"使用经验: {skill}")

        # Primary interest category
        interests = prefs.get("interests", [])
        if interests:
            signals.append(f"兴趣: {', '.join(interests[:3])}")

        # Favorite brands (top 3 only)
        brands = prefs.get("favorite_brands", [])
        if brands:
            signals.append(f"偏好品牌: {', '.join(brands[:3])}")

        # Budget range
        for key in prefs:
            if "budget" in key.lower():
                signals.append(f"{key}: {prefs[key]}")

        if not signals:
            return ""

        return "用户画像: " + "; ".join(signals)

    def _build_tier2(
        self,
        profile: dict[str, Any],
        purchases: list[dict[str, Any]],
        conversations: list[dict[str, Any]],
        *,
        query: str,
        intent: str,
    ) -> str:
        """Build query-relevant context block."""
        parts: list[str] = []
        query_keywords = self._extract_keywords(query)
        budget_remaining = self._t2_budget

        # ---- Relevant purchases ----
        if purchases and query_keywords:
            relevant_purchases = self._filter_by_keywords(
                purchases, query_keywords, key_fn=lambda p: p.get("product", "")
            )
            if relevant_purchases:
                purchase_strs = []
                for p in relevant_purchases[:3]:
                    purchase_strs.append(
                        f"{p.get('date', '?')[:7]} {p.get('product', '?')} ¥{p.get('price', '?')}"
                    )
                block = "历史购买: " + " | ".join(purchase_strs)
                parts.append(block)
                budget_remaining -= len(block)

        # ---- Relevant past conversations ----
        if conversations and budget_remaining > 50:
            relevant_convos = self._filter_conversations(conversations, intent, query_keywords)
            if relevant_convos:
                convo_strs = []
                for c in relevant_convos[:2]:
                    q = c.get("query", "")[:80]
                    convo_strs.append(f"曾问: {q}")
                block = "相关对话: " + "; ".join(convo_strs)
                # Only add if within budget
                if len(block) <= budget_remaining:
                    parts.append(block)

        # ---- Relevant preference details ----
        if profile and query_keywords and budget_remaining > 30:
            prefs = profile.get("preferences", {})
            matched_prefs = {}
            for key, val in prefs.items():
                key_str = str(key) + str(val)
                if any(kw in key_str for kw in query_keywords):
                    matched_prefs[key] = val
            if matched_prefs:
                block = "相关偏好: " + str(matched_prefs)
                if len(block) <= budget_remaining:
                    parts.append(block)

        return "\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(query: str) -> set[str]:
        """Extract category/semantic keywords from a Chinese query.

        Simple tokenization — not a full NLP pipeline.  We split on common
        delimiters and keep tokens of 2+ characters.
        """
        tokens = re.split(r"[，。,.\s!！?？、：:的了吗呢吧在是和]", query)
        keywords: set[str] = set()
        for t in tokens:
            t = t.strip()
            if len(t) >= 2 and not t.isdigit():
                keywords.add(t)
        return keywords

    @staticmethod
    def _filter_by_keywords(
        items: list[dict[str, Any]],
        keywords: set[str],
        key_fn: callable,
    ) -> list[dict[str, Any]]:
        """Return items whose key matches any keyword."""
        result = []
        for item in items:
            text = key_fn(item)
            if any(kw in text for kw in keywords):
                result.append(item)
        return result

    @staticmethod
    def _filter_conversations(
        conversations: list[dict[str, Any]],
        intent: str,
        keywords: set[str],
    ) -> list[dict[str, Any]]:
        """Return conversations matching intent or keywords."""
        result = []
        for conv in conversations:
            conv_intent = conv.get("intent", "")
            conv_query = conv.get("query", "")

            # Exact intent match — high signal
            if intent and conv_intent == intent:
                result.append(conv)
                continue

            # Keyword overlap in the past query
            conv_kw = MemoryCompressor._extract_keywords(conv_query)
            if conv_kw & keywords:
                result.append(conv)
                continue

        return result
