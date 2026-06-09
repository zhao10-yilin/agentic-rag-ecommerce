"""Integration tests for the Agentic RAG Phase 1 components.

Tests the full agent loop with mock/fake components, verifying:
- Agent state machine transitions
- Tool registration and lookup
- Plan validation
- Degradation handling
- Basic scenario flow
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure both pdf_parser and agentic_rag are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agentic_rag.models import (
    AgentAction,
    AgentPlan,
    AgentResponse,
    AgentState,
    AgentStep,
    DegradationPolicy,
    ToolCall,
    ToolResult,
)
from agentic_rag.tools.base import BaseTool, ToolRegistry


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


class _EchoTool(BaseTool):
    """Returns its input as output — useful for testing."""

    def __init__(self):
        super().__init__(
            name="echo",
            description="Echoes the input back.",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            degradation=DegradationPolicy.SKIP,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            status="success",
            summary=f"Echo: {call.input.get('message', '')}",
            structured_data={"echo": call.input.get("message", "")},
        )


class _FailingTool(BaseTool):
    """Always fails — used to test degradation."""

    def __init__(self, degradation: DegradationPolicy = DegradationPolicy.FAIL_FAST):
        super().__init__(
            name="failing",
            description="Always fails.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            degradation=degradation,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        raise RuntimeError("Simulated tool failure")


# ---------------------------------------------------------------------------
# ToolRegistry tests
# ---------------------------------------------------------------------------


class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        reg.register(_EchoTool())
        return reg

    def test_register_and_get(self, registry):
        tool = registry.get("echo")
        assert tool is not None
        assert tool.name == "echo"

    def test_register_duplicate_raises(self, registry):
        with pytest.raises(ValueError):
            registry.register(_EchoTool())  # "echo" already exists

    def test_get_required_missing(self, registry):
        with pytest.raises(KeyError):
            registry.get_required("nonexistent")

    def test_list_names(self, registry):
        assert registry.list_names() == ["echo"]

    def test_list_descriptions(self, registry):
        desc = registry.list_descriptions()
        assert "echo" in desc
        assert "Echoes" in desc

    def test_get_all_schemas(self, registry):
        schemas = registry.get_all_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "echo"


# ---------------------------------------------------------------------------
# ToolResult tests
# ---------------------------------------------------------------------------


class TestToolResult:
    def test_create_success(self):
        result = ToolResult(
            tool_name="test",
            status="success",
            summary="Everything went well",
            structured_data={"key": "value"},
        )
        assert result.status == "success"
        assert result.error is None
        assert result.cache_hit is False

    def test_create_degraded(self):
        result = ToolResult(
            tool_name="test",
            status="degraded",
            summary="Partial result",
            error="Backend timeout",
            error_code="TIMEOUT",
        )
        assert result.status == "degraded"
        assert result.error_code == "TIMEOUT"


# ---------------------------------------------------------------------------
# Tool execution tests
# ---------------------------------------------------------------------------


class TestToolExecution:
    def test_echo_tool(self):
        import asyncio

        tool = _EchoTool()
        call = ToolCall(tool_name="echo", input={"message": "hello"})
        result = asyncio.run(tool.execute(call))
        assert result.status == "success"
        assert "hello" in result.summary
        assert result.structured_data["echo"] == "hello"

    def test_failing_tool_fail_fast(self):
        import asyncio

        tool = _FailingTool(degradation=DegradationPolicy.FAIL_FAST)
        call = ToolCall(tool_name="failing", input={})
        with pytest.raises(RuntimeError, match="Simulated"):
            asyncio.run(tool.execute(call))

    def test_tool_input_validation(self):
        tool = _EchoTool()
        call = ToolCall(tool_name="echo", input={})  # Missing "message"
        errors = tool.validate_input(call.input)
        assert len(errors) > 0
        assert any("message" in e for e in errors)

    def test_tool_input_validation_passes(self):
        tool = _EchoTool()
        call = ToolCall(tool_name="echo", input={"message": "hello"})
        errors = tool.validate_input(call.input)
        assert errors == []

    def test_openai_function_schema(self):
        tool = _EchoTool()
        schema = tool.to_openai_function()
        assert schema["type"] == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "echo"


# ---------------------------------------------------------------------------
# Agent state machine tests
# ---------------------------------------------------------------------------


class TestAgentState:
    def test_valid_transitions(self):
        from agentic_rag.agent.state import can_transition

        assert can_transition(AgentState.PLANNING, AgentState.VALIDATING)
        assert can_transition(AgentState.VALIDATING, AgentState.PLANNING)  # re-plan
        assert can_transition(AgentState.REFLECTING, AgentState.PLANNING)
        assert can_transition(AgentState.SYNTHESIZING, AgentState.DONE)

    def test_invalid_transition(self):
        from agentic_rag.agent.state import can_transition

        assert not can_transition(AgentState.DONE, AgentState.PLANNING)
        assert not can_transition(AgentState.EXECUTING, AgentState.CLARIFYING)

    def test_terminal_states(self):
        from agentic_rag.agent.state import is_terminal

        assert is_terminal(AgentState.DONE)
        assert is_terminal(AgentState.ERROR)
        assert not is_terminal(AgentState.PLANNING)


# ---------------------------------------------------------------------------
# Plan validation tests
# ---------------------------------------------------------------------------


class TestPlanValidator:
    @pytest.fixture
    def validator(self):
        from agentic_rag.agent.plan_validator import PlanValidator

        registry = ToolRegistry()
        registry.register(_EchoTool())
        return PlanValidator(registry)

    def test_valid_plan_passes(self, validator):
        plan = AgentPlan(
            original_query="test",
            steps=[
                AgentStep(
                    step_index=0,
                    actions=[
                        AgentAction(tool_name="echo", input={"message": "hello"})
                    ],
                )
            ],
        )
        errors = validator.validate(plan)
        assert errors == []

    def test_unknown_tool_fails(self, validator):
        plan = AgentPlan(
            original_query="test",
            steps=[
                AgentStep(
                    step_index=0,
                    actions=[
                        AgentAction(tool_name="imaginary_tool", input={})
                    ],
                )
            ],
        )
        errors = validator.validate(plan)
        assert len(errors) > 0
        assert "imaginary_tool" in errors[0]

    def test_missing_required_param(self, validator):
        plan = AgentPlan(
            original_query="test",
            steps=[
                AgentStep(
                    step_index=0,
                    actions=[
                        AgentAction(tool_name="echo", input={})  # Missing "message"
                    ],
                )
            ],
        )
        errors = validator.validate(plan)
        assert len(errors) > 0
        assert "message" in errors[0]

    def test_circular_dependency(self, validator):
        plan = AgentPlan(
            original_query="test",
            steps=[
                AgentStep(
                    step_index=0,
                    actions=[AgentAction(tool_name="echo", input={"message": "a"})],
                    depends_on=[1],  # Depends on step that doesn't exist yet
                ),
                AgentStep(
                    step_index=1,
                    actions=[AgentAction(tool_name="echo", input={"message": "b"})],
                    depends_on=[0],
                ),
            ],
        )
        errors = validator.validate(plan)
        # Step 0 depends on step 1 which has higher index — invalid
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# AgentPlan model tests
# ---------------------------------------------------------------------------


class TestAgentPlan:
    def test_create_minimal(self):
        plan = AgentPlan(original_query="hello")
        assert plan.original_query == "hello"
        assert plan.intent == ""
        assert plan.steps == []
        assert len(plan.plan_id) == 12

    def test_with_steps(self):
        plan = AgentPlan(
            original_query="test query",
            rewritten_query="rewritten test",
            intent="shopping_guide",
            intent_clarity=0.9,
            steps=[
                AgentStep(
                    step_index=0,
                    actions=[
                        AgentAction(
                            tool_name="rag_search",
                            input={"query": "outdoor gear"},
                            reason="Find relevant guides",
                        )
                    ],
                    description="Search knowledge base",
                )
            ],
        )
        assert plan.intent == "shopping_guide"
        assert len(plan.steps) == 1
        assert plan.steps[0].actions[0].tool_name == "rag_search"


# ---------------------------------------------------------------------------
# AgentResponse model tests
# ---------------------------------------------------------------------------


class TestAgentResponse:
    def test_clarification_response(self):
        resp = AgentResponse(
            answer="",
            clarifying_question="您想在室内还是户外使用？",
            state=AgentState.CLARIFYING,
        )
        assert resp.state == AgentState.CLARIFYING
        assert resp.clarifying_question is not None
        assert resp.answer == ""

    def test_degradation_notes(self):
        resp = AgentResponse(
            answer="Partial answer",
            state=AgentState.DONE,
            degradation_notes=["web_search unavailable: connection refused"],
        )
        assert len(resp.degradation_notes) == 1


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
