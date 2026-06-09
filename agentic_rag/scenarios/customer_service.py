"""Scenario 1 — Smart Customer Service / Shopping Guide.

Handles vague shopping intents like:
- "I'm going to an outdoor music festival, what gear do I need?"
- "What should I prepare for a beach wedding?"
- "Recommend gear for hiking in winter"

This scenario demonstrates:
- Intent clarification (outdoor activity → weather check → gear list)
- Multi-tool orchestration (RAG + web search)
- Degradation handling (web search unavailable → RAG-only response)
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_rag.agent.core import PlanAndExecuteAgent
from agentic_rag.models import AgentResponse
from agentic_rag.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


class CustomerServiceScenario:
    """Orchestrates the smart customer service / shopping guide scenario.

    This scenario pre-loads scenario-relevant tools and provides
    domain-specific context to improve planning accuracy.

    Parameters
    ----------
    agent:
        A configured :class:`PlanAndExecuteAgent`.
    """

    def __init__(self, agent: PlanAndExecuteAgent) -> None:
        self._agent = agent

    async def handle(
        self,
        message: str,
        *,
        user_id: str | None = None,
    ) -> AgentResponse:
        """Process a shopping guide / customer service request.

        The agent will:
        1. Assess intent clarity — ask clarifying question if needed
        2. Search the knowledge base for relevant guides and gear lists
        3. Search the web for real-time info (weather, events)
        4. Synthesize a complete, personalised recommendation
        """
        logger.info("CustomerServiceScenario: handling '%s'", message[:80])
        response = await self._agent.run(message, user_id=user_id)
        return response

    async def continue_with_response(
        self,
        user_response: str,
        previous_response: AgentResponse,
        *,
        user_id: str | None = None,
    ) -> AgentResponse:
        """Continue after the user answers a clarifying question."""
        return await self._agent.continue_with_clarification(
            user_response, previous_response, user_id=user_id
        )


def create_customer_service_scenario(
    llm_gateway: Any,
    tool_registry: ToolRegistry,
    *,
    memory_manager: Any = None,
    reflector: Any = None,
) -> CustomerServiceScenario:
    """Factory function to create a pre-configured customer service scenario.

    Parameters
    ----------
    llm_gateway:
        Existing ``LLMGateway`` from ``pdf_parser.rag``.
    tool_registry:
        Registry with at least ``rag_search`` and ``web_search`` tools.
    memory_manager:
        Optional long-term memory.
    reflector:
        Optional LLM reflector.
    """
    agent = PlanAndExecuteAgent(
        llm_gateway=llm_gateway,
        tool_registry=tool_registry,
        memory_manager=memory_manager,
        reflector=reflector,
    )
    return CustomerServiceScenario(agent)
