"""FastAPI routes for the Agentic RAG system.

Mount on the main FastAPI app::

    from agentic_rag.api import router as agent_router
    app.include_router(agent_router, prefix="/api/v1/agent")
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentic_rag.config import get_settings
from agentic_rag.models import AgentResponse, AgentState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agentic RAG"])

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Request to the agentic chat endpoint."""

    message: str = Field(..., description="The user's message.")
    user_id: str | None = Field(None, description="User identifier for personalization.")
    session_id: str | None = Field(None, description="Session identifier for multi-turn.")
    clarification_response: str | None = Field(
        None,
        description="User's response to a clarifying question. "
                    "Required if the previous response asked a question.",
    )


class ChatResponse(BaseModel):
    """Response from the agentic chat endpoint."""

    answer: str = Field(default="", description="The agent's answer (empty if clarifying).")
    clarifying_question: str | None = Field(
        None, description="If set, the agent needs clarification — display this to the user."
    )
    state: str = Field(default="done")
    sources: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls_made: int = Field(default=0)
    degradation_notes: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0

    model_config = {"frozen": True}


class StatsResponse(BaseModel):
    agent_state: str
    tools_registered: int
    tools_list: list[str]


# ---------------------------------------------------------------------------
# Lazy agent singleton
# ---------------------------------------------------------------------------

_agent: Any = None
_tool_registry: Any = None
_metrics: Any = None


def _get_agent():
    """Return the process-level agent singleton."""
    global _agent, _tool_registry, _metrics

    if _agent is None:
        logger.info("Initialising PlanAndExecuteAgent (loading models)...")

        from pdf_parser.rag.llm_gateway import LLMGateway

        from agentic_rag.agent.core import PlanAndExecuteAgent
        from agentic_rag.observability.metrics import AgentMetrics
        from agentic_rag.reflection.reflector import Reflector
        from agentic_rag.tools.base import ToolRegistry
        from agentic_rag.tools.rag_tool import RAGSearchTool
        from agentic_rag.tools.web_search_tool import WebSearchTool

        settings = get_settings()

        # LLM
        try:
            llm = LLMGateway(
                model=settings.llm_model,
                light_model=settings.llm_light_model_effective,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        except ValueError as exc:
            raise RuntimeError(
                "LLMGateway not available. Set AGENTIC_RAG_LLM_API_KEY "
                "(or DEEPSEEK_API_KEY / OPENAI_API_KEY)."
            ) from exc

        # Tools
        _tool_registry = ToolRegistry()
        _tool_registry.register_many(
            RAGSearchTool(),
            WebSearchTool(),
        )

        # Reflector
        reflector = Reflector(llm)

        # Agent
        _agent = PlanAndExecuteAgent(
            llm_gateway=llm,
            tool_registry=_tool_registry,
            reflector=reflector,
        )

        # Metrics
        _metrics = AgentMetrics()

        logger.info("Agent ready with %d tools", _tool_registry.count)

    return _agent, _tool_registry, _metrics


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> dict[str, Any]:
    """Main agent chat endpoint.

    Send a message and receive either a final answer or a clarifying question.

    **Clarification flow**::

        # Turn 1 — agent asks for clarification
        POST /chat {"message": "推荐一个好东西"}
        → {"clarifying_question": "您是想找什么类型的好东西呢？...", "state": "clarifying"}

        # Turn 2 — user responds to clarification
        POST /chat {"message": "推荐一个好东西", "clarification_response": "想买咖啡机"}
        → {"answer": "根据您的需求...", "state": "done"}
    """
    t0 = time.perf_counter()
    agent, registry, metrics = _get_agent()

    try:
        # Handle clarification follow-up
        if body.clarification_response:
            # We need the previous response for context — stored client-side
            from agentic_rag.models import AgentPlan, AgentResponse as AR

            # Build a temporary previous response for the continuation
            prev = AR(
                answer="",
                clarifying_question="(previous clarification)",
                state=AgentState.CLARIFYING,
                plan=AgentPlan(original_query=body.message),
            )
            response = await agent.continue_with_clarification(
                body.clarification_response,
                prev,
                user_id=body.user_id,
            )
        else:
            response = await agent.run(
                body.message,
                user_id=body.user_id,
                session_id=body.session_id,
            )

        # Record metrics
        if metrics:
            if response.state == AgentState.CLARIFYING:
                metrics.record_clarification(
                    response.plan.intent if response.plan else "unknown"
                )
            for record in response.tool_calls_made:
                metrics.record_tool_call(
                    record.tool_name,
                    record.result.status,
                    record.result.elapsed_ms,
                )
            if response.plan:
                metrics.record_plan_steps(len(response.plan.steps))

        elapsed = time.perf_counter() - t0
        return {
            "answer": response.answer,
            "clarifying_question": response.clarifying_question,
            "state": response.state.value,
            "sources": response.sources,
            "tool_calls_made": len(response.tool_calls_made),
            "degradation_notes": response.degradation_notes,
            "elapsed_seconds": round(elapsed, 3),
        }

    except Exception as exc:
        logger.exception("Agent chat failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats", response_model=StatsResponse)
def get_stats() -> dict[str, Any]:
    """Return agent and tool registry statistics."""
    agent, registry, _ = _get_agent()
    return {
        "agent_state": "ready",
        "tools_registered": registry.count,
        "tools_list": registry.list_names(),
    }


@router.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    try:
        _get_agent()
        return {"status": "healthy"}
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


@router.get("/metrics")
def metrics_endpoint():
    """Prometheus metrics endpoint."""
    from fastapi.responses import Response

    _, _, metrics = _get_agent()
    if metrics is None:
        return Response(content=b"# Metrics not available\n", media_type="text/plain")

    body, content_type = metrics.get_metrics_response()
    return Response(content=body, media_type=content_type)
