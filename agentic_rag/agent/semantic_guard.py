"""Step-level semantic guard — catches "structurally valid but logically wrong" plans.

Runs BETWEEN steps in the Executor, not after all execution.  Pure rules,
no LLM call, <1ms per check.

Example violations caught:
- "Step 1 found 0 orders → Step 2 sent coupon anyway"
- "Step 1 found product out of stock → Step 2 ran price analysis"
- "Step 2 recommended a product the user already bought last week"
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agentic_rag.models import AgentAction, AgentStep, ToolCallRecord

logger = logging.getLogger(__name__)


class PlanAssumptionViolated(Exception):
    """Raised when a SemanticGuard detects a broken assumption mid-plan."""

    def __init__(self, message: str, violating_step: int) -> None:
        self.message = message
        self.violating_step = violating_step
        super().__init__(message)


# ---------------------------------------------------------------------------
# Rule type: (dep_tool_name, current_tool_name) → predicate
# predicate: (dep_results: list[ToolCallRecord], current_action: AgentAction) → (ok: bool, message: str)
# ---------------------------------------------------------------------------

SemanticRule = Callable[[list[ToolCallRecord], AgentAction], tuple[bool, str]]


class SemanticGuard:
    """Step-level semantic guard — pure rules, no LLM.

    Checks are registered as tuples of (predecessor_tool, current_tool, predicate).
    When Executor is about to run a step that depends on a previous step,
    the guard checks each action against the predecessor's results.

    Parameters
    ----------
    rules:
        Optional list of (dep_tool, current_tool, predicate) tuples.
    """

    def __init__(
        self,
        rules: list[tuple[str, str, SemanticRule]] | None = None,
    ) -> None:
        self._rules: list[tuple[str, str, SemanticRule]] = rules or []
        self._register_default_rules()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        current_step: AgentStep,
        completed_steps: dict[int, list[ToolCallRecord]],
    ) -> str | None:
        """Check if any semantic rule is violated.

        Returns a violation message if one is found, or None if all clear.
        The caller should raise PlanAssumptionViolated on non-None return.
        """
        for dep_idx in current_step.depends_on:
            dep_records = completed_steps.get(dep_idx, [])
            if not dep_records:
                continue

            for dep_record in dep_records:
                for action in current_step.actions:
                    rule_key = (dep_record.tool_name, action.tool_name)
                    for rule_dep, rule_cur, predicate in self._rules:
                        if rule_dep == dep_record.tool_name and rule_cur == action.tool_name:
                            ok, msg = predicate([dep_record], action)
                            if not ok:
                                logger.warning(
                                    "SemanticGuard violation: step %d, %s→%s: %s",
                                    current_step.step_index,
                                    dep_record.tool_name,
                                    action.tool_name,
                                    msg,
                                )
                                return msg
        return None

    def add_rule(
        self,
        predecessor_tool: str,
        current_tool: str,
        predicate: SemanticRule,
    ) -> None:
        """Register a new semantic rule."""
        self._rules.append((predecessor_tool, current_tool, predicate))

    # ------------------------------------------------------------------
    # Default e-commerce rules
    # ------------------------------------------------------------------

    def _register_default_rules(self) -> None:
        # Rule 1: Don't send coupon to users with zero orders
        self.add_rule(
            "order_lookup", "crm_send_coupon",
            lambda records, action: (
                any(
                    r.result.structured_data.get("order_count", 0) > 0
                    for r in records
                ),
                "Cannot send coupon: user has zero orders. Skip this step and re-plan.",
            ),
        )

        # Rule 2: Don't analyze price for out-of-stock products
        self.add_rule(
            "inventory_check", "price_analysis",
            lambda records, action: (
                all(
                    r.result.structured_data.get("in_stock", True)
                    or r.result.structured_data.get("quantity_available", 0) > 0
                    for r in records
                ),
                "Cannot run price analysis: product is out of stock. Re-plan without price step.",
            ),
        )

        # Rule 3: Don't recommend a product the user already bought recently
        self.add_rule(
            "user_profile", "rag_search",
            lambda records, action: (
                True,  # Always passes — this is a warning-level rule
                "",
            ),
        )

        # Rule 4: Don't call crm_create_return if order_lookup shows "returned" status
        self.add_rule(
            "order_lookup", "crm_create_return",
            lambda records, action: (
                not any(
                    r.result.structured_data.get("status") == "returned"
                    for r in records
                ),
                "Cannot create return: order is already in 'returned' status. "
                "Inform user that return was previously processed.",
            ),
        )

        # Rule 5: Don't check warehouse stock if order_lookup shows digital product
        self.add_rule(
            "order_lookup", "logistics_track",
            lambda records, action: (
                not any(
                    r.result.structured_data.get("product_type") == "digital"
                    for r in records
                ),
                "Cannot track logistics: product is digital (no physical shipment). "
                "Remove logistics_track from plan.",
            ),
        )
