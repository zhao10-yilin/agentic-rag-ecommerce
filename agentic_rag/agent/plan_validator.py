"""Lightweight plan validation — runs before execution to catch errors early.

Checks performed:
1. Every tool name exists in the ToolRegistry.
2. Required parameters for each tool are present.
3. Parameter types match the tool's JSON Schema.
4. ``depends_on`` references are valid (exist and are < current step index).
5. No circular dependencies.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_rag.agent.input_sanitizer import (
    CONFIRMATION_REQUIRED_TOOLS,
    INTENT_TOOL_WHITELIST,
    get_disallowed_tools,
)
from agentic_rag.models import AgentPlan, AgentStep
from agentic_rag.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


class PlanValidationError(Exception):
    """Raised when a plan fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class PlanValidator:
    """Validates an :class:`AgentPlan` against a :class:`ToolRegistry`.

    Parameters
    ----------
    tool_registry:
        The registry to validate tool names and schemas against.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._registry = tool_registry

    def validate(self, plan: AgentPlan) -> list[str]:
        """Return a list of validation error messages (empty = valid).

        Does NOT raise — callers decide whether to reject or warn.
        """
        errors: list[str] = []

        # Check each step
        for step in plan.steps:
            errors.extend(self._validate_step(step, len(plan.steps)))

        # Check circular dependencies across all steps
        errors.extend(self._check_circular(plan.steps))

        # ---- Intent-tool whitelist audit ----
        # Planner might have been prompt-injected into calling tools
        # that don't belong in the classified intent.
        # Skip audit if intent is empty or unknown (treat as general).
        if plan.intent and plan.intent in INTENT_TOOL_WHITELIST:
            intent_violations = get_disallowed_tools(plan.steps, plan.intent)
            for step_idx, tool_name in intent_violations:
                errors.append(
                    f"Step {step_idx}: 工具 '{tool_name}' 不在意图 '{plan.intent}' 的允许列表中。"
                    f" 这可能是 Planner 被注入或规划错误。"
                )

        # ---- Confirmation-required tools audit ----
        # Tools like order_create, crm_create_return need explicit
        # user confirmation before the Executor will run them.
        for step in plan.steps:
            for action in step.actions:
                if action.tool_name in CONFIRMATION_REQUIRED_TOOLS:
                    logger.info(
                        "Plan includes confirmation-required tool: %s in step %d",
                        action.tool_name, step.step_index,
                    )

        if errors:
            logger.warning("Plan validation found %d error(s): %s", len(errors), errors[:5])
        else:
            logger.debug("Plan validation passed: %d steps", len(plan.steps))

        return errors

    def validate_or_raise(self, plan: AgentPlan) -> None:
        """Like ``validate`` but raises :class:`PlanValidationError` on failure."""
        errors = self.validate(plan)
        if errors:
            raise PlanValidationError(errors)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _validate_step(self, step: AgentStep, total_steps: int) -> list[str]:
        errors: list[str] = []

        # Check depends_on references
        for dep in step.depends_on:
            if dep < 0:
                errors.append(
                    f"Step {step.step_index}: negative dependency index {dep}"
                )
            elif dep >= step.step_index:
                errors.append(
                    f"Step {step.step_index}: depends_on {dep} must be < {step.step_index}"
                )
            elif dep >= total_steps:
                errors.append(
                    f"Step {step.step_index}: depends_on {dep} exceeds total steps {total_steps}"
                )

        # Check each action
        for action in step.actions:
            tool = self._registry.get(action.tool_name)
            if tool is None:
                errors.append(
                    f"Step {step.step_index}: unknown tool '{action.tool_name}'. "
                    f"Available: {self._registry.list_names()}"
                )
                continue

            # Validate input against tool schema
            input_errors = tool.validate_input(action.input)
            for ie in input_errors:
                errors.append(f"Step {step.step_index}, tool '{action.tool_name}': {ie}")

        return errors

    @staticmethod
    def _check_circular(steps: list[AgentStep]) -> list[str]:
        """Simple DFS cycle detection on the dependency graph."""
        errors: list[str] = []

        # Build adjacency: step_index -> list of steps it depends on
        dep_graph: dict[int, list[int]] = {s.step_index: s.depends_on for s in steps}

        visited: set[int] = set()
        rec_stack: set[int] = set()

        def has_cycle(node: int) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for dep in dep_graph.get(node, []):
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    errors.append(f"Circular dependency detected involving step {node}")
                    return True
            rec_stack.discard(node)
            return False

        for step_idx in dep_graph:
            if step_idx not in visited:
                has_cycle(step_idx)

        return errors
