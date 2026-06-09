"""Business-logic contracts for tools — what JSON Schema cannot express.

JSON Schema can validate:
    "order_id must be a string"

It cannot validate:
    "user must be VIP to return items"
    "order must be in 'completed' status before return"
    "refund amount must not exceed original order price"
    "cannot return an item that was already returned"

Contracts fill this gap.  Each tool declares its business rules as Python
callables that are:

1. **Testable offline** — property-based tests generate edge cases from
   each contract and verify the tool rejects them.
2. **Enforceable online** — if ``enforce=True``, the Executor's
   pre-condition check runs before calling ``tool.execute()``.
3. **Auditable** — failed pre-conditions are logged with the exact rule
   that was violated, not a generic error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from agentic_rag.models import ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Contract primitives
# ---------------------------------------------------------------------------


class ContractPhase(str, Enum):
    PRECONDITION = "precondition"    # Must hold BEFORE execute()
    POSTCONDITION = "postcondition"  # Must hold AFTER execute()
    INVARIANT = "invariant"          # Must hold for EVERY successful call


@dataclass
class Contract:
    """A single business-rule contract attached to a tool.

    Parameters
    ----------
    name:
        Human-readable rule name (e.g. "仅VIP用户可退货").
    phase:
        When the check runs.
    predicate:
        For PRECONDITION: ``(call: ToolCall, context: dict) -> (bool, str)``.
        For POSTCONDITION: ``(call, result, context) -> (bool, str)``.
        Returns (passed: bool, failure_message: str).
    context_keys:
        Keys the predicate needs from ``context`` (e.g. ["user_tier", "order_status"]).
    enforce:
        If True, the Executor blocks the tool call on precondition failure.
        If False, the contract is logged but not enforced (warning mode).
    test_inputs:
        Explicit edge-case inputs for test generation.  Each entry is a
        call input dict paired with a ``context`` dict, and the expected
        result (should_pass: bool).
    """

    name: str
    phase: ContractPhase
    predicate: Callable[..., tuple[bool, str]]
    context_keys: list[str] = field(default_factory=list)
    enforce: bool = True
    test_inputs: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Contract checker
# ---------------------------------------------------------------------------


class ContractChecker:
    """Runs pre/post-condition contracts against tool calls.

    Parameters
    ----------
    contracts:
        The list of contracts to check.
    """

    def __init__(self, contracts: list[Contract]) -> None:
        self._contracts = contracts

    def check_preconditions(
        self, call: ToolCall, context: dict[str, Any]
    ) -> list[tuple[Contract, str]]:
        """Run all PRECONDITION contracts. Returns [(violated_contract, message)]."""
        violations: list[tuple[Contract, str]] = []
        for c in self._contracts:
            if c.phase != ContractPhase.PRECONDITION:
                continue
            # Gather context
            ctx = {k: context.get(k) for k in c.context_keys}
            try:
                passed, msg = c.predicate(call, ctx)
                if not passed:
                    violations.append((c, msg))
            except Exception as exc:
                violations.append((c, f"Contract '{c.name}' evaluation error: {exc}"))
        return violations

    def check_postconditions(
        self, call: ToolCall, result: ToolResult, context: dict[str, Any]
    ) -> list[tuple[Contract, str]]:
        """Run all POSTCONDITION contracts."""
        violations: list[tuple[Contract, str]] = []
        for c in self._contracts:
            if c.phase != ContractPhase.POSTCONDITION:
                continue
            ctx = {k: context.get(k) for k in c.context_keys}
            try:
                passed, msg = c.predicate(call, result, ctx)
                if not passed:
                    violations.append((c, msg))
            except Exception as exc:
                violations.append((c, f"Contract '{c.name}' evaluation error: {exc}"))
        return violations

    @property
    def all_contracts(self) -> list[Contract]:
        return list(self._contracts)


# ---------------------------------------------------------------------------
# Example contracts (for crm_create_return tool)
# ---------------------------------------------------------------------------


def _vip_only_predicate(call: ToolCall, ctx: dict) -> tuple[bool, str]:
    """仅 VIP 用户可发起退货。"""
    user_tier = ctx.get("user_tier", "regular")
    if user_tier not in ("vip", "platinum"):
        return False, f"用户等级为 '{user_tier}'，仅 VIP 及以上可发起退货"
    return True, ""


def _order_completed_predicate(call: ToolCall, ctx: dict) -> tuple[bool, str]:
    """仅“已完成”状态的订单可发起退货。"""
    order_status = ctx.get("order_status", "")
    if order_status != "completed":
        return False, f"订单状态为 '{order_status}'，仅‘已完成’订单可退货"
    return True, ""


def _not_already_returned_predicate(call: ToolCall, ctx: dict) -> tuple[bool, str]:
    """不可对已退货的订单重复发起退货。"""
    already_returned = ctx.get("already_returned", False)
    if already_returned:
        return False, "该订单已发起过退货，不可重复操作"
    return True, ""


def _refund_not_exceed_price_predicate(
    call: ToolCall, result: ToolResult, ctx: dict
) -> tuple[bool, str]:
    """退款金额不得超过原订单金额。"""
    original_price = ctx.get("original_price", 0)
    refund_amount = result.structured_data.get("refund_amount", 0)
    if refund_amount > original_price:
        return False, (
            f"退款金额 ¥{refund_amount} 超出原订单金额 ¥{original_price}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Contract registry for the CRM Return tool
# ---------------------------------------------------------------------------


# Registry of contracts for crm_create_return tool
CRM_RETURN_CONTRACTS = [
    Contract(
        name="仅VIP用户可退货",
        phase=ContractPhase.PRECONDITION,
        predicate=_vip_only_predicate,
        context_keys=["user_tier"],
        enforce=True,
        test_inputs=[
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"user_tier": "vip"}, "should_pass": True},
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"user_tier": "regular"}, "should_pass": False},
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"user_tier": ""}, "should_pass": False},
        ],
    ),
    Contract(
        name="仅已完成订单可退货",
        phase=ContractPhase.PRECONDITION,
        predicate=_order_completed_predicate,
        context_keys=["order_status"],
        enforce=True,
        test_inputs=[
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"order_status": "completed"}, "should_pass": True},
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"order_status": "pending"}, "should_pass": False},
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"order_status": "returned"}, "should_pass": False},
        ],
    ),
    Contract(
        name="不可重复退货",
        phase=ContractPhase.PRECONDITION,
        predicate=_not_already_returned_predicate,
        context_keys=["already_returned"],
        enforce=True,
        test_inputs=[
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"already_returned": False}, "should_pass": True},
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"already_returned": True}, "should_pass": False},
        ],
    ),
    Contract(
        name="退款金额不超过原订单金额",
        phase=ContractPhase.POSTCONDITION,
        predicate=_refund_not_exceed_price_predicate,
        context_keys=["original_price"],
        enforce=True,
        test_inputs=[
            {"input": {"order_id": "ORD-001", "reason": "瑕疵"},
             "context": {"original_price": 299.0}, "should_pass": True,
             "mock_result": ToolResult(
                 tool_name="crm_create_return", status="success",
                 summary="退款 ¥299.0", structured_data={"refund_amount": 299.0},
             )},
        ],
    ),
]

# Alias for auto-generated test imports (tool name + _CONTRACTS convention)
CRM_CREATE_RETURN_CONTRACTS = CRM_RETURN_CONTRACTS


# ---------------------------------------------------------------------------
# Test generator for contracts
# ---------------------------------------------------------------------------


def generate_contract_tests(contracts: list[Contract], tool_name: str) -> str:
    """Generate pytest code for a tool's business contracts.

    This complements the schema-based auto-generator — schema tests check
    structural validity; contract tests check business rules.
    """
    contract_module = "agentic_rag.tools.contracts"
    contract_list_name = f"{tool_name.upper()}_CONTRACTS"

    lines = [
        '"""Auto-generated contract tests for business rules."""',
        "import pytest",
        "from agentic_rag.tools.contracts import ContractChecker, ContractPhase",
        "from agentic_rag.models import ToolResult, ToolCall",
        f"from {contract_module} import {contract_list_name}",
        "",
        f"CONTRACTS = {contract_list_name}",
        "",
    ]

    for i, c in enumerate(contracts):
        if not c.test_inputs:
            continue

        lines.append(f"@pytest.mark.parametrize('test_case', [")
        for tc in c.test_inputs:
            lines.append(f"    {tc!r},")
        lines.append("])")
        lines.append(
            f"def test_contract_{tool_name}_{c.name.replace(' ', '_')}(test_case):"
        )
        lines.append(f'    """Contract: {c.name}"""')
        lines.append(f"    checker = ContractChecker([CONTRACTS[{i}]])")
        lines.append(f"    call = ToolCall(tool_name='{tool_name}', input=test_case['input'])")

        if c.phase == ContractPhase.PRECONDITION:
            lines.append(f"    ctx = test_case.get('context', {{}})")
            lines.append(f"    violations = checker.check_preconditions(call, ctx)")
            lines.append(f"    if test_case['should_pass']:")
            lines.append(f"        assert violations == [], f'Expected pass, got: {{violations}}'")
            lines.append(f"    else:")
            lines.append(f"        assert violations != [], f'Expected violation, got none'")
        elif c.phase == ContractPhase.POSTCONDITION:
            lines.append(f"    ctx = test_case.get('context', {{}})")
            lines.append(f"    mock_result = test_case.get('mock_result')")
            lines.append(f"    if mock_result is None:")
            lines.append(f"        pytest.skip('No mock_result provided')")
            lines.append(f"    violations = checker.check_postconditions(call, mock_result, ctx)")
            lines.append(f"    if test_case['should_pass']:")
            lines.append(f"        assert violations == [], f'Expected pass, got: {{violations}}'")
            lines.append(f"    else:")
            lines.append(f"        assert violations != [], f'Expected violation, got none'")
        lines.append("")

    return "\n".join(lines)
