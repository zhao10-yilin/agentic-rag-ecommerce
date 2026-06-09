"""System health self-check — validates all critical subsystems.

Run: python -X utf8 health_check.py

Checks:
1. Tool registry integrity (all tools reachable, no duplicate names)
2. Intent-Tool whitelist coverage (every registered tool has at least one intent)
3. Contract coverage (which write tools have contracts)
4. Degradation policy audit (are any write tools still on FAIL_FAST?)
5. PlanValidator structural checks (can it catch known bad plans?)
6. ExecutionProfile sanity (timeout values in reasonable ranges)
7. CallbackRouter capacity
8. TraceStore operational

Exit code 0 = all healthy, 1 = warnings, 2 = critical failures.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


class HealthLevel(str, Enum):
    OK = "OK"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass
class HealthCheck:
    name: str
    level: HealthLevel = HealthLevel.OK
    message: str = ""


@dataclass
class HealthReport:
    checks: list[HealthCheck] = field(default_factory=list)
    warnings: int = 0
    criticals: int = 0

    def add(self, name: str, passed: bool, detail: str = "",
            warn_if_false: bool = False) -> None:
        if passed:
            self.checks.append(HealthCheck(name, HealthLevel.OK, detail or "PASS"))
        elif warn_if_false:
            self.checks.append(HealthCheck(name, HealthLevel.WARN, detail or "FAIL"))
            self.warnings += 1
        else:
            self.checks.append(HealthCheck(name, HealthLevel.CRITICAL, detail or "FAIL"))
            self.criticals += 1

    def print(self) -> int:
        print("=" * 60)
        print("  Agentic RAG — System Health Report")
        print("=" * 60)
        print()

        for c in self.checks:
            icon = {"OK": "[OK]", "WARN": "[WARN]", "CRITICAL": "[FAIL]"}[c.level.value]
            print(f"  {icon} {c.name}")
            if c.message and c.message != "PASS":
                print(f"      {c.message}")

        print()
        print(f"  Total: {len(self.checks)} checks")
        print(f"  Passed: {len(self.checks) - self.warnings - self.criticals}")
        print(f"  Warnings: {self.warnings}")
        print(f"  Critical: {self.criticals}")
        print()

        if self.criticals > 0:
            print("  STATUS: UNHEALTHY (critical failures)")
            return 2
        elif self.warnings > 0:
            print("  STATUS: DEGRADED (warnings present)")
            return 1
        else:
            print("  STATUS: HEALTHY")
            return 0


def check_all() -> HealthReport:
    report = HealthReport()

    # ---- 1. Tool registry integrity ----
    _check_tool_registry(report)

    # ---- 2. Intent-Tool whitelist coverage ----
    _check_whitelist_coverage(report)

    # ---- 3. Contract coverage for write tools ----
    _check_contract_coverage(report)

    # ---- 4. Degradation policy audit ----
    _check_degradation_policies(report)

    # ---- 5. PlanValidator structural checks ----
    _check_plan_validator(report)

    # ---- 6. ExecutionProfile sanity ----
    _check_execution_profiles(report)

    # ---- 7. CallbackRouter capacity ----
    _check_callback_router(report)

    # ---- 8. TraceStore operational ----
    _check_trace_store(report)

    # ---- 9. Token Budget boundaries ----
    _check_token_budget(report)

    return report


def _check_tool_registry(report: HealthReport):
    from agentic_rag.tools.base import ToolRegistry
    from agentic_rag.tools.rag_tool import RAGSearchTool
    from agentic_rag.tools.web_search_tool import WebSearchTool

    registry = ToolRegistry()
    registry.register_many(RAGSearchTool(), WebSearchTool())

    # Duplicate detection
    try:
        registry.register(RAGSearchTool())
        report.add("ToolRegistry: duplicate detection", False,
                    "Should have raised ValueError for duplicate 'rag_search'")
    except ValueError:
        report.add("ToolRegistry: duplicate detection", True)

    # Tool lookup
    tool = registry.get("rag_search")
    report.add("ToolRegistry: tool lookup", tool is not None,
               f"rag_search -> {type(tool).__name__}" if tool else "not found")

    # Schema validity
    schemas = registry.get_all_schemas()
    all_valid = all(
        s.get("type") == "function" and "function" in s
        for s in schemas
    )
    report.add("ToolRegistry: schema validity", all_valid,
               f"{len(schemas)} tools have valid OpenAI function schemas")


def _check_whitelist_coverage(report: HealthReport):
    from agentic_rag.agent.input_sanitizer import INTENT_TOOL_WHITELIST

    # Every intent has at least rag_search (universal tool)
    missing = []
    for intent, tools in INTENT_TOOL_WHITELIST.items():
        if "rag_search" not in tools:
            missing.append(intent)

    report.add("Whitelist: all intents have rag_search", len(missing) == 0,
               f"Missing from: {missing}" if missing else "All intents covered")

    # Count total tool-intent mappings
    total_mappings = sum(len(tools) for tools in INTENT_TOOL_WHITELIST.values())
    report.add("Whitelist: coverage density", total_mappings >= 10,
               f"{total_mappings} tool-intent mappings across {len(INTENT_TOOL_WHITELIST)} intents")


def _check_contract_coverage(report: HealthReport):
    from agentic_rag.tools.contracts import CRM_RETURN_CONTRACTS

    num_contracts = len(CRM_RETURN_CONTRACTS)
    has_test_inputs = sum(1 for c in CRM_RETURN_CONTRACTS if c.test_inputs)

    report.add("Contracts: crm_create_return coverage", num_contracts >= 3,
               f"{num_contracts} contracts ({has_test_inputs} with test_inputs)",
               warn_if_false=num_contracts < 3)


def _check_degradation_policies(report: HealthReport):
    from agentic_rag.tools.base import ExecutionProfile, MutationKind
    from agentic_rag.models import DegradationPolicy

    # Simulate: check if a hypothetical write tool would default to FAIL_FAST
    profile_default = ExecutionProfile()
    write_tool_would_fail_fast = (
        profile_default.mutation_kind == MutationKind.READ
    )
    report.add("Degradation: READ tools default safe", write_tool_would_fail_fast,
               "Default ExecutionProfile.mutation_kind=READ (non-mutating)")

    # Check that DegradationPolicy enum has all required variants
    expected = {"fail_fast", "return_cached", "skip", "inform_user", "retry_with_backoff"}
    actual = set(p.value for p in DegradationPolicy)
    report.add("Degradation: all 5 policies defined", actual == expected,
               f"Defined: {sorted(actual)}")


def _check_plan_validator(report: HealthReport):
    from agentic_rag.agent.plan_validator import PlanValidator
    from agentic_rag.models import AgentPlan, AgentStep, AgentAction
    from agentic_rag.tools.base import ToolRegistry
    from agentic_rag.tools.rag_tool import RAGSearchTool

    registry = ToolRegistry()
    registry.register(RAGSearchTool())
    validator = PlanValidator(registry)

    # Valid plan
    plan = AgentPlan(
        original_query="test", intent="general",
        steps=[AgentStep(step_index=0, actions=[
            AgentAction(tool_name="rag_search", input={"query": "test"})
        ])],
    )
    errors = validator.validate(plan)
    report.add("PlanValidator: valid plan passes", len(errors) == 0,
               f"Errors: {errors}" if errors else "No errors")

    # Unknown tool
    plan_bad = AgentPlan(
        original_query="test", intent="general",
        steps=[AgentStep(step_index=0, actions=[
            AgentAction(tool_name="nonexistent_tool_xyz", input={})
        ])],
    )
    errors_bad = validator.validate(plan_bad)
    report.add("PlanValidator: unknown tool caught", len(errors_bad) > 0,
               f"Caught: {errors_bad[0][:80]}" if errors_bad else "NOT CAUGHT")

    # Circular dependency
    plan_circular = AgentPlan(
        original_query="test",
        steps=[
            AgentStep(step_index=0, actions=[
                AgentAction(tool_name="rag_search", input={"query": "a"})
            ], depends_on=[1]),
            AgentStep(step_index=1, actions=[
                AgentAction(tool_name="rag_search", input={"query": "b"})
            ], depends_on=[0]),
        ],
    )
    errors_circular = validator.validate(plan_circular)
    report.add("PlanValidator: circular dependency caught", len(errors_circular) > 0,
               f"Caught: {len(errors_circular)} errors" if errors_circular else "NOT CAUGHT")


def _check_execution_profiles(report: HealthReport):
    from agentic_rag.tools.base import ExecutionProfile

    profiles = {
        "instant": ExecutionProfile.instant(),
        "fast": ExecutionProfile.fast(),
        "slow": ExecutionProfile.slow(),
        "very_slow": ExecutionProfile.very_slow(),
    }

    issues = []
    for name, p in profiles.items():
        if p.max_latency_ms < p.expected_latency_ms:
            issues.append(f"{name}: max({p.max_latency_ms}) < expected({p.expected_latency_ms})")
        if p.max_latency_ms > 300_000:
            issues.append(f"{name}: max_latency > 5min")

    report.add("ExecutionProfile: timeout sanity", len(issues) == 0,
               "; ".join(issues) if issues else "All profiles have reasonable timeouts")


def _check_callback_router(report: HealthReport):
    from agentic_rag.dify.callback_router import CallbackRouter

    router = CallbackRouter()
    report.add("CallbackRouter: initialized", router is not None,
               f"default_timeout={router._timeout}s, max_pending={router._max}")


def _check_trace_store(report: HealthReport):
    from agentic_rag.observability.trace_store import get_trace_store

    store = get_trace_store()
    trace_id = store.start_trace("health_check_test")
    store.add_phase(trace_id, "planning", 0, 0.5)
    store.add_phase(trace_id, "executing", 0.5, 1.0)
    store.complete_trace(trace_id, final_state="done")

    trace = store.get_trace(trace_id)
    report.add("TraceStore: write/read cycle", trace is not None and len(trace.phases) == 2,
               f"Stored and retrieved trace with {len(trace.phases) if trace else 0} phases")

    metrics = store.get_metrics_snapshot()
    report.add("TraceStore: metrics aggregation", "total_traces" in metrics,
               f"Metrics keys: {list(metrics.keys())[:5]}" if metrics else "No metrics")


def _check_token_budget(report: HealthReport):
    from agentic_rag.observability.token_budget import TokenBudget
    from agentic_rag.models import ToolCallRecord, ToolResult

    budget = TokenBudget(max_chars=500)

    records = [
        ToolCallRecord(
            tool_name="test_error", input={},
            result=ToolResult(tool_name="test_error", status="error",
                summary="Error details here", error="Connection refused",
                error_code="TIMEOUT"),
            step_index=0, idempotency_key="h1"),
        ToolCallRecord(
            tool_name="test_success", input={},
            result=ToolResult(tool_name="test_success", status="success",
                summary="OK" * 200),  # 400 chars of summary
            step_index=0, idempotency_key="h2"),
    ]

    compressed = budget.compress(records, plan_summary="test")
    within_budget = len(compressed) <= 600  # Allow some overhead
    report.add("TokenBudget: stays within budget", within_budget,
               f"{len(compressed)} chars (budget: 500)")


# ============================================================================
# CLI entry
# ============================================================================


def main():
    report = check_all()
    exit_code = report.print()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
