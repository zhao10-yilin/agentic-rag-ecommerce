"""Unified LLM gateway for query rewriting, retrieval decisions, and answer generation.

Supports any OpenAI-compatible API (DeepSeek, Qwen, OpenAI, local vLLM).
Configured entirely through environment variables:

``DEEPSEEK_API_KEY`` / ``OPENAI_API_KEY``
    API key for the LLM provider.

``DEEPSEEK_BASE_URL`` / ``OPENAI_BASE_URL``
    Base URL override (defaults to DeepSeek).

``RAG_LLM_MODEL``
    Model name override (default: ``deepseek-chat``).

``RAG_LLM_LIGHT_MODEL``
    Lighter/cheaper model for query rewriting and retrieval decisions
    (default: same as ``RAG_LLM_MODEL``).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pdf_parser.rag.models import QueryPlan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SELF_RAG_SYSTEM_PROMPT = """\
你是一个检索增强生成（RAG）系统的查询分析器。你的任务是：

1. 判断用户的查询是否需要检索外部知识库才能准确回答。
2. 如果需要检索，生成 2-3 个改写后的查询变体，并生成一个假设答案（HyDE）。

## 判断标准
- **不需要检索**：常识问题、纯粹的闲聊、翻译任务、简单的计算、编程问题（不涉及特定领域知识）
- **需要检索**：涉及特定文件、法规、合同、公司制度、专业知识、数据查询等问题

## 输出格式
你必须只输出一个 JSON 对象，不要添加任何其他文字：

```json
{
  "needs_retrieval": true,
  "strategy": "retrieve",
  "rewritten_queries": ["改写查询1", "改写查询2"],
  "hyde_doc": "一个假设的答案段落，模拟知识库中可能包含的内容"
}
```

如果不需要检索：
```json
{
  "needs_retrieval": false,
  "strategy": "direct",
  "rewritten_queries": [],
  "hyde_doc": null
}
```"""

SELF_RAG_CHAT_SYSTEM_PROMPT = """\
你是一个检索增强生成（RAG）系统的查询分析器。你的任务是分析**多轮对话**中用户的最新消息。

## 核心任务
1. 根据对话历史，将用户的模糊指代（如"那第二条呢"、"上面提到的那个法条"）改写成完整、独立的问题。
2. 判断是否需要检索外部知识库。
3. 如果用户的新消息与对话历史中已讨论的话题完全无关，开启新话题。

## 输出格式
你必须只输出一个 JSON 对象：

```json
{
  "needs_retrieval": true,
  "strategy": "retrieve",
  "rewritten_queries": ["改写后的完整查询1", "改写查询2"],
  "hyde_doc": "一个假设的答案段落"
}
```

如果不需要检索（如用户说"谢谢"、"还有吗"等闲聊）：
```json
{
  "needs_retrieval": false,
  "strategy": "direct",
  "rewritten_queries": [],
  "hyde_doc": null
}
```"""

RAG_ANSWER_SYSTEM_PROMPT = """\
你是一个基于知识库的智能问答助手。你的回答必须严格基于下面提供的【参考文档】。
如果参考文档中没有相关信息，请如实说"根据现有资料，我无法回答这个问题"，不要编造任何内容。

## 回答要求
- 引用参考文档中的具体内容，注明来源（如"根据《XXX》第X条..."）
- 如果参考文档中有多个相关段落，综合它们的信息给出全面回答
- 语言专业、准确、简洁
- 如果用户问题与参考文档无关，礼貌地说明你的知识范围限制"""

RAG_CHAT_ANSWER_PROMPT = """\
你是一个基于知识库的智能问答助手。你正在与用户进行多轮对话。你的回答必须严格基于下面提供的【参考文档】。

## 对话要求
- 理解对话历史中的上下文，回应用户的追问和指代
- 引用参考文档中的具体内容，注明来源
- 如果参考文档中没有相关信息，如实说"根据现有资料，我无法回答这个问题"
- 如果用户的新问题与之前的对话话题无关，当作新话题处理
- 保持回答连贯，与之前的对话语气一致
- 语言专业、准确、简洁"""


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class LLMGateway:
    """Unified async LLM client for the RAG pipeline.

    Parameters
    ----------
    model:
        Model name for answer generation (default: ``deepseek-chat``).
    light_model:
        Model for lightweight tasks — query rewriting + retrieval decisions.
        Defaults to *model* if not set.
    api_key:
        API key.  Falls back to ``DEEPSEEK_API_KEY`` or ``OPENAI_API_KEY``.
    base_url:
        Base URL.  Falls back to ``DEEPSEEK_BASE_URL`` or DeepSeek default.
    max_retries:
        Number of retries on transient API errors.
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        light_model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self._model = model or os.environ.get("RAG_LLM_MODEL", "deepseek-chat")
        self._light_model = light_model or os.environ.get(
            "RAG_LLM_LIGHT_MODEL", self._model
        )
        self._timeout = timeout

        # Resolve API key
        resolved_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not resolved_key:
            raise ValueError(
                "No API key found for LLMGateway. "
                "Set DEEPSEEK_API_KEY or OPENAI_API_KEY."
            )
        self._api_key = resolved_key
        self._base_url = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )

        self._client: Any = None
        self._async_client: Any = None

        logger.info(
            "LLMGateway ready: model=%s light=%s base=%s",
            self._model,
            self._light_model,
            self._base_url,
        )

    # ------------------------------------------------------------------
    # Lazy init
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=3,
            )
        return self._client

    def _get_async_client(self):
        if self._async_client is None:
            from openai import AsyncOpenAI

            self._async_client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=3,
            )
        return self._async_client

    # ------------------------------------------------------------------
    # Query rewriting + Self-RAG decision
    # ------------------------------------------------------------------

    def rewrite_query(
        self,
        query: str,
        *,
        context: str | None = None,
    ) -> QueryPlan:
        """Analyze *query* and produce a :class:`QueryPlan`.

        This is a **synchronous** method (blocks on the LLM API call).
        For async usage call ``await rewrite_query_async(...)``.
        """
        client = self._get_client()

        user_msg = f"用户查询：{query}"
        if context:
            user_msg += f"\n\n对话上下文：{context}"

        response = client.chat.completions.create(
            model=self._light_model,
            messages=[
                {"role": "system", "content": SELF_RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=1024,
        )

        raw = response.choices[0].message.content or "{}"
        parsed = self._parse_plan_json(raw, query)

        logger.info(
            "Query plan: needs_retrieval=%s strategy=%s queries=%d",
            parsed.needs_retrieval,
            parsed.strategy,
            len(parsed.rewritten_queries),
        )
        return parsed

    async def rewrite_query_async(
        self,
        query: str,
        *,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> QueryPlan:
        """Async variant of :meth:`rewrite_query`.

        When *history* is provided (multi-turn chat), the LLM resolves
        anaphora like "那第二条呢" into the full referenced query by
        examining the conversation history.
        """
        client = self._get_async_client()

        messages: list[dict[str, str]] = []

        if history:
            # Multi-turn: use chat-specific prompt with full conversation
            messages.append({"role": "system", "content": SELF_RAG_CHAT_SYSTEM_PROMPT})
            for h in history[-6:]:  # Last 3 exchanges (6 messages) is enough context
                messages.append({"role": h["role"], "content": h["content"]})
            messages.append({"role": "user", "content": f"【用户最新消息】\n{query}\n\n请分析这条消息并输出 JSON。"})
        else:
            # Single-turn: original behaviour
            messages.append({"role": "system", "content": SELF_RAG_SYSTEM_PROMPT})
            user_msg = f"用户查询：{query}"
            if context:
                user_msg += f"\n\n对话上下文：{context}"
            messages.append({"role": "user", "content": user_msg})

        response = await client.chat.completions.create(
            model=self._light_model,
            messages=messages,
            temperature=0.0,
            max_tokens=1024,
        )

        raw = response.choices[0].message.content or "{}"
        return self._parse_plan_json(raw, query)

    # ------------------------------------------------------------------
    # Answer generation
    # ------------------------------------------------------------------

    def generate_answer(
        self,
        query: str,
        contexts: list[str],
        *,
        system_prompt: str | None = None,
    ) -> str:
        """Generate a grounded answer using the retrieved *contexts*.

        Parameters
        ----------
        query:
            The user's original question.
        contexts:
            Retrieved document chunks (big-chunk text) to ground the answer in.
        system_prompt:
            Override the default RAG answer system prompt.
        """
        client = self._get_client()

        if not contexts:
            return "根据现有资料，我无法回答这个问题。"

        # Build context block
        context_block_parts: list[str] = []
        for i, ctx in enumerate(contexts, 1):
            context_block_parts.append(f"### 参考文档 {i}\n{ctx}")
        context_block = "\n\n".join(context_block_parts)

        user_msg = (
            f"【参考文档】\n\n{context_block}\n\n"
            f"【用户问题】\n{query}\n\n"
            f"请基于参考文档回答用户问题。"
        )

        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt or RAG_ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        return response.choices[0].message.content or ""

    async def generate_answer_async(
        self,
        query: str,
        contexts: list[str],
        *,
        system_prompt: str | None = None,
    ) -> str:
        """Async variant of :meth:`generate_answer`."""
        client = self._get_async_client()

        if not contexts:
            return "根据现有资料，我无法回答这个问题。"

        context_block_parts: list[str] = []
        for i, ctx in enumerate(contexts, 1):
            context_block_parts.append(f"### 参考文档 {i}\n{ctx}")
        context_block = "\n\n".join(context_block_parts)

        user_msg = (
            f"【参考文档】\n\n{context_block}\n\n"
            f"【用户问题】\n{query}\n\n"
            f"请基于参考文档回答用户问题。"
        )

        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt or RAG_ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        return response.choices[0].message.content or ""

    async def generate_chat_answer_async(
        self,
        query: str,
        contexts: list[str],
        *,
        history: list[dict[str, str]] | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """Generate an answer in multi-turn conversation context.

        Includes the conversation *history* so the LLM can maintain
        coherence across turns and handle follow-up questions naturally.
        """
        client = self._get_async_client()

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt or RAG_CHAT_ANSWER_PROMPT},
        ]

        if history:
            for h in history[-8:]:
                messages.append({"role": h["role"], "content": h["content"]})

        if contexts:
            context_block_parts: list[str] = []
            for i, ctx in enumerate(contexts, 1):
                context_block_parts.append(f"### 参考文档 {i}\n{ctx}")
            context_block = "\n\n".join(context_block_parts)
            user_msg = (
                f"【参考文档】\n\n{context_block}\n\n"
                f"【用户问题】\n{query}\n\n"
                f"请基于参考文档回答用户问题。"
            )
        else:
            user_msg = f"【用户问题】\n{query}"

        messages.append({"role": "user", "content": user_msg})

        response = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )

        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # JSON parser (robust against LLM formatting quirks)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_plan_json(raw: str, query: str) -> QueryPlan:
        """Extract a JSON object from possibly-noisy LLM output."""
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON, falling back to default plan: %s", raw[:200])
            return QueryPlan(
                original_query=query,
                needs_retrieval=True,
                strategy="retrieve",
                rewritten_queries=[query],
            )

        return QueryPlan(
            original_query=query,
            needs_retrieval=bool(data.get("needs_retrieval", True)),
            strategy=str(data.get("strategy", "retrieve")),
            rewritten_queries=data.get("rewritten_queries", []) or [],
            hyde_doc=data.get("hyde_doc"),
        )
