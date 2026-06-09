"""Pydantic data models for the Agentic RAG system.

All models are immutable (``frozen=True``) for safe cross-thread /
async-task sharing.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Agent state machine
# ---------------------------------------------------------------------------


class AgentState(str, Enum):
    CLARIFYING = "clarifying"
    PLANNING = "planning"
    VALIDATING = "validating"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    SYNTHESIZING = "synthesizing"
    DONE = "done"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Tool degradation
# ---------------------------------------------------------------------------


class DegradationPolicy(str, Enum):
    FAIL_FAST = "fail_fast"
    RETURN_CACHED = "return_cached"
    SKIP = "skip"
    INFORM_USER = "inform_user"
    RETRY_WITH_BACKOFF = "retry_with_backoff"


# ---------------------------------------------------------------------------
# Tool models
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    """A single tool invocation within an agent step."""

    tool_name: str = Field(..., description="Registered tool name.")
    input: dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the tool."
    )
    idempotency_key: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:16],
        description="Unique key for deduplication.",
    )

    model_config = {"frozen": True}


class ToolResult(BaseModel):
    """Standardised result from any tool execution."""

    tool_name: str
    status: Literal["success", "degraded", "error", "timeout"]
    summary: str = Field(
        ..., description="LLM-readable summary of the result (max 500 chars)."
    )
    structured_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Program-usable structured output.",
    )
    error: str | None = None
    error_code: str | None = None
    cache_hit: bool = False
    elapsed_ms: float = 0.0

    model_config = {"frozen": True}


class ToolCallRecord(BaseModel):
    """Immutable record of a completed tool call (for tracing)."""

    tool_name: str
    input: dict[str, Any]
    result: ToolResult
    timestamp: float = Field(default_factory=time.time)
    idempotency_key: str
    step_index: int = 0

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Agent plan models
# ---------------------------------------------------------------------------


class AgentAction(BaseModel):
    """A planned action within a step — resolves to a ToolCall at runtime."""

    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(
        default="", description="Why this action is needed (for traceability)."
    )

    model_config = {"frozen": True}


class AgentStep(BaseModel):
    """One step in an AgentPlan — contains one or more parallel Actions."""

    step_index: int
    actions: list[AgentAction] = Field(
        ..., min_length=1, description="1+ tool actions that can run in parallel."
    )
    depends_on: list[int] = Field(
        default_factory=list, description="Step indices that must complete first."
    )
    description: str = Field(
        default="", description="Human-readable step description."
    )

    model_config = {"frozen": True}


class AgentPlan(BaseModel):
    """Ordered plan produced by the Planner."""

    plan_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    original_query: str
    rewritten_query: str = ""
    intent: str = Field(
        default="", description="Classified intent: shopping_guide, recommendation, operations, supply_chain, general"
    )
    intent_clarity: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="How clearly the intent is understood (0=ambiguous, 1=crystal clear).",
    )
    clarifying_question: str | None = Field(
        default=None, description="If clarity < threshold, a question to ask the user."
    )
    steps: list[AgentStep] = Field(default_factory=list)
    final_synthesis_hint: str = Field(
        default="",
        description="Guidance from the planner about how to synthesise the final answer.",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    """Final response returned to the caller."""

    answer: str = Field(..., description="Synthesised answer for the user.")
    clarifying_question: str | None = Field(
        default=None, description="If set, the agent is asking for clarification."
    )
    state: AgentState = AgentState.DONE
    sources: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls_made: list[ToolCallRecord] = Field(default_factory=list)
    plan: AgentPlan | None = None
    elapsed_seconds: float = 0.0
    degradation_notes: list[str] = Field(default_factory=list)
    # --- Attribution ---
    trace_id: str = Field(
        default="", description="Unique ID linking this response to downstream events."
    )
    experiment_group: str = Field(
        default="control", description="A/B test group: 'control' | 'treatment_v1' | ..."
    )
    clarification_round: int = Field(
        default=0, description="How many clarification rounds have occurred so far."
    )
    recommended_product_ids: list[str] = Field(
        default_factory=list,
        description="Product IDs surfaced in this answer, for attribution join.",
    )

    model_config = {"frozen": True}


class AgentMemoryState(BaseModel):
    """Snapshot of relevant memory loaded at the start of an agent run."""

    user_id: str | None = None
    user_profile: dict[str, Any] = Field(default_factory=dict)
    recent_conversations: list[dict[str, Any]] = Field(default_factory=list)
    relevant_entities: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"frozen": True}
