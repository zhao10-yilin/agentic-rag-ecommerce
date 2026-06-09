"""Token budget with diagnostic-priority compression.

When tool results accumulate beyond the LLM's effective context window,
we must compress — but NOT uniformly.  Failed tools get more diagnostic
space; successful tools get a one-line summary.

Priority tiers:
    1. FAILED/ERROR tools — preserve full diagnostic info (error msg, context)
    2. DEGRADED tools — keep summary, note what was missing
    3. SUCCESS tools — compress to one line each
    4. Plan metadata — always kept, always short
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_rag.models import ToolCallRecord

logger = logging.getLogger(__name__)


class TokenBudget:
    """Compresses tool results to fit within a character budget.

    Parameters
    ----------
    max_chars:
        Hard ceiling on total output characters.
    success_max_chars_per_tool:
        Per-tool budget for successful results.
    error_max_chars_per_tool:
        Per-tool budget for error/degraded results (larger).
    """

    def __init__(
        self,
        *,
        max_chars: int = 2500,
        success_max_chars_per_tool: int = 150,
        error_max_chars_per_tool: int = 400,
    ) -> None:
        self._max = max_chars
        self._success_per = success_max_chars_per_tool
        self._error_per = error_max_chars_per_tool

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compress(
        self,
        records: list[ToolCallRecord],
        *,
        plan_summary: str = "",
    ) -> str:
        """Compress tool results into a budget-constrained string.

        Algorithm:
        1. Plan summary takes its space first (always included, always short).
        2. Error/degraded records get priority allocation (diagnostic value).
        3. Success records share the remaining budget equally.
        4. If budget exhausted, truncate with a truncation notice.
        """
        parts: list[str] = []

        # Plan summary
        if plan_summary:
            parts.append(plan_summary)
            budget = self._max - len(plan_summary)
        else:
            budget = self._max

        # Separate by status
        errors = [r for r in records if r.result.status in ("error", "timeout")]
        degraded = [r for r in records if r.result.status == "degraded"]
        success = [r for r in records if r.result.status == "success"]

        # Phase 1: Error tools — full diagnostic space
        for r in errors:
            line = self._format_error_record(r, budget)
            if len(line) > budget:
                line = line[:budget - 50] + "\n[...truncated — budget exhausted]"
            parts.append(line)
            budget -= len(line)
            if budget < 100:
                parts.append("[TOKEN BUDGET EXHAUSTED — remaining results omitted]")
                return "\n".join(parts)

        # Phase 2: Degraded tools — medium space
        for r in degraded:
            line = self._format_degraded_record(r, min(self._error_per, budget))
            if budget > len(line):
                parts.append(line)
                budget -= len(line)
            elif budget > 50:
                parts.append(f"[DEGRADED] {r.tool_name}: [degraded — details omitted, budget exhausted]")
                budget -= 50

        # Phase 3: Success tools — share remaining budget equally
        if success and budget > 200:
            per_success = max(60, budget // len(success))
            for r in success:
                line = self._format_success_record(r, per_success)
                if budget > len(line):
                    parts.append(line)
                    budget -= len(line)
                else:
                    parts.append(f"[OK] {r.tool_name}: [success — omitted, budget full]")
                    break
        elif success:
            # Budget too tight for details — just list tool names
            names = ", ".join(r.tool_name for r in success)
            parts.append(f"[OK] Success tools ({len(success)}): {names}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Format helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_error_record(record: ToolCallRecord, budget: int) -> str:
        """Error tools get maximum diagnostic detail."""
        summary = record.result.summary[:min(300, budget // 2)]
        error = record.result.error or "(no error detail)"
        error_code = record.result.error_code or "UNKNOWN"
        return (
            f"[ERROR] {record.tool_name} [{error_code}]\n"
            f"   Result: {summary}\n"
            f"   Diagnostic: {error[:min(200, budget // 2)]}"
        )

    @staticmethod
    def _format_degraded_record(record: ToolCallRecord, budget: int) -> str:
        """Degraded tools get a medium summary."""
        summary = record.result.summary[:min(200, budget)]
        return f"[DEGRADED] {record.tool_name}: {summary}"

    @staticmethod
    def _format_success_record(record: ToolCallRecord, budget: int) -> str:
        """Success tools get a one-line summary."""
        summary = record.result.summary[:min(budget - 30, 120)]
        return f"[OK] {record.tool_name}: {summary}"
