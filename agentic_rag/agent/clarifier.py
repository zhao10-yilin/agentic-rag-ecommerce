"""Clarification mechanism — handles ambiguous user intents.

When the Planner assigns a low ``intent_clarity`` score, the agent
enters the CLARIFYING state and asks the user a targeted question
before committing to a plan.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agentic_rag.config import get_settings
from agentic_rag.models import AgentPlan

logger = logging.getLogger(__name__)

CLARIFY_SYSTEM_PROMPT = """\
你是一个电商智能助手。用户刚才提出了一个模糊的请求。你需要生成一个简洁、友好的追问来澄清用户需求。

## 追问原则
1. 一次只问一个问题（最关键的那个）。
2. 提供 2-3 个具体选项帮助用户快速回答。
3. 用口语化的、温暖的中文表达。
4. 如果用户的问题涉及商品推荐，优先澄清使用场景或预算。
5. 如果用户的问题涉及售后，优先澄清订单号或问题类型。

## 输出格式
直接输出追问文本，不要添加引号或 JSON。"""


class Clarifier:
    """Generates targeted clarifying questions for ambiguous intents.

    Parameters
    ----------
    llm_gateway:
        The existing ``LLMGateway`` from ``pdf_parser.rag``.
    """

    def __init__(self, llm_gateway: Any) -> None:
        self._llm = llm_gateway
        self._settings = get_settings()

    def needs_clarification(self, plan: AgentPlan) -> bool:
        """Return ``True`` if the plan's clarity is below threshold."""
        return plan.intent_clarity < self._settings.intent_clarity_threshold

    async def generate_question(self, user_message: str, plan: AgentPlan) -> str:
        """Generate a clarifying question.

        If the planner already produced a good question, use it directly.
        Otherwise, use the LLM to generate one.
        """
        # Use the planner's own question if available and reasonable
        if plan.clarifying_question and len(plan.clarifying_question) > 5:
            logger.info("Using planner-generated clarifying question")
            return plan.clarifying_question

        # Generate via LLM
        question = await self._generate_via_llm(user_message, plan)
        return question

    async def enrich_with_clarification(
        self, plan: AgentPlan, user_response: str
    ) -> str:
        """Combine the original query with the user's clarification.

        Returns an enriched query string for replanning.
        """
        enriched = (
            f"【原始请求】{plan.original_query}\n"
            f"【用户补充说明】{user_response}\n"
            f"请基于以上完整信息重新规划。"
        )
        return enriched

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _generate_via_llm(
        self, user_message: str, plan: AgentPlan
    ) -> str:
        client = self._llm._get_async_client()

        user_prompt = (
            f"用户模糊请求：{user_message}\n"
            f"意图类别：{plan.intent}\n"
            f"请生成一个追问来澄清用户需求。"
        )

        response = await client.chat.completions.create(
            model=self._llm._light_model,
            messages=[
                {"role": "system", "content": CLARIFY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=256,
        )
        return response.choices[0].message.content or "能否再详细描述一下您的需求？"
