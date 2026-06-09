"""Fast rule-based validation for intermediate tool results.

Runs after each tool execution — checks constraints that must hold true
regardless of the LLM's opinion (stock > 0, prices in range, policies).
Much faster than LLM reflection (~1ms vs ~2s).
"""

from __future__ import annotations

from typing import Any

from agentic_rag.models import ToolCallRecord


class RuleValidator:
    """Validates tool results against domain rules.

    Checks are additive — register new rules via :meth:`add_rule`.
    """

    def __init__(self) -> None:
        self._rules: list[callable] = [
            self._check_stock_non_negative,
            self._check_price_positive,
            self._check_source_chunks_non_empty,
        ]

    def add_rule(self, rule: callable) -> None:
        """Register a new validation rule.

        *rule* must be a callable ``(ToolCallRecord) -> list[str]`` that
        returns a list of error messages (empty = valid).
        """
        self._rules.append(rule)

    def validate(self, record: ToolCallRecord) -> list[str]:
        """Run all rules against a tool result. Returns error messages."""
        errors: list[str] = []
        for rule in self._rules:
            try:
                result = rule(record)
                errors.extend(result)
            except Exception as exc:
                errors.append(f"Rule {rule.__name__} failed: {exc}")
        return errors

    def validate_all(self, records: list[ToolCallRecord]) -> dict[str, list[str]]:
        """Return ``{tool_name: [errors]}`` for all records."""
        result: dict[str, list[str]] = {}
        for r in records:
            errs = self.validate(r)
            if errs:
                result[r.tool_name] = errs
        return result

    # ------------------------------------------------------------------
    # Built-in rules
    # ------------------------------------------------------------------

    @staticmethod
    def _check_stock_non_negative(record: ToolCallRecord) -> list[str]:
        """Inventory results must not claim negative stock."""
        data = record.result.structured_data
        if "quantity_available" in data and data["quantity_available"] < 0:
            return [f"库存数量为负数: {data['quantity_available']}"]
        return []

    @staticmethod
    def _check_price_positive(record: ToolCallRecord) -> list[str]:
        """Prices must be positive."""
        data = record.result.structured_data
        for key in ("price", "our_price", "suggested_price"):
            if key in data and data[key] is not None and data[key] <= 0:
                return [f"价格无效: {key}={data[key]}"]
        return []

    @staticmethod
    def _check_source_chunks_non_empty(record: ToolCallRecord) -> list[str]:
        """RAG search results should have sources when status is success."""
        if record.result.status != "success":
            return []
        if record.tool_name in ("rag_search", "rag_chat"):
            data = record.result.structured_data
            sources = data.get("sources", [])
            answer = data.get("answer", "")
            # Only flag if we claimed success but have zero sources AND a non-fallback answer
            if (
                not sources
                and answer
                and "无法回答" not in answer
                and "没有找到" not in answer
            ):
                return ["RAG 搜索返回成功但没有检索到任何来源文档"]
        return []
