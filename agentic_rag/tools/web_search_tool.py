"""Web search tool — external real-time information retrieval.

Provides time-sensitive data that may not exist in the knowledge base,
such as weather forecasts, event schedules, news, and competitor updates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from agentic_rag.models import DegradationPolicy, ToolCall, ToolResult
from agentic_rag.tools.base import BaseTool

logger = logging.getLogger(__name__)

WEB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query for the external web search engine.",
        },
        "num_results": {
            "type": "integer",
            "description": "Number of search results to return (default 5, max 10).",
            "default": 5,
        },
    },
    "required": ["query"],
}


class WebSearchTool(BaseTool):
    """Search the public web for real-time information.

    Falls back to cached results on error, since web search is often
    a secondary signal and partial answers are better than none.
    """

    def __init__(self) -> None:
        super().__init__(
            name="web_search",
            description=(
                "搜索互联网获取实时信息，如天气、活动时间、新闻、价格行情等。"
                "当问题涉及实时数据或知识库中没有的公开信息时使用。"
            ),
            parameters=WEB_SEARCH_SCHEMA,
            degradation=DegradationPolicy.RETURN_CACHED,
            cache_ttl_seconds=300,
        )
        self._cache: dict[str, dict[str, Any]] = {}

    async def execute(self, call: ToolCall) -> ToolResult:
        t0 = time.perf_counter()
        query: str = call.input.get("query", "")
        num_results: int = min(call.input.get("num_results", 5), 10)

        cache_key = hashlib.sha256(
            (query + str(num_results)).encode()
        ).hexdigest()[:16]

        # Check cache
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            age = time.time() - entry["timestamp"]
            if age < self.cache_ttl_seconds:
                result = json.loads(entry["data"])
                elapsed_ms = (time.perf_counter() - t0) * 1000
                return ToolResult(
                    tool_name=self.name,
                    status="success",
                    summary=result.get("summary", ""),
                    structured_data=result,
                    cache_hit=True,
                    elapsed_ms=round(elapsed_ms, 2),
                )

        try:
            result = await self._do_search(query, num_results)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            # Update cache
            self._cache[cache_key] = {
                "data": json.dumps(result, ensure_ascii=False),
                "timestamp": time.time(),
            }
            # Evict oldest if more than 500 entries
            while len(self._cache) > 500:
                oldest = min(self._cache, key=lambda k: self._cache[k]["timestamp"])
                del self._cache[oldest]

            return ToolResult(
                tool_name=self.name,
                status="success",
                summary=result.get("summary", ""),
                structured_data=result,
                elapsed_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.exception("Web search failed for: %s", query[:80])

            # Try cache fallback (degraded)
            if cache_key in self._cache:
                entry = self._cache[cache_key]
                result = json.loads(entry["data"])
                return ToolResult(
                    tool_name=self.name,
                    status="degraded",
                    summary=f"[缓存结果 — 搜索服务不可用]\n{result.get('summary', '')}",
                    structured_data=result,
                    error=str(exc),
                    error_code="WEB_SEARCH_FALLBACK_CACHE",
                    elapsed_ms=round(elapsed_ms, 2),
                )

            return ToolResult(
                tool_name=self.name,
                status="error",
                summary=f"网络搜索失败：{exc}",
                structured_data={},
                error=str(exc),
                error_code="WEB_SEARCH_ERROR",
                elapsed_ms=round(elapsed_ms, 2),
            )

    async def _do_search(self, query: str, num_results: int) -> dict[str, Any]:
        """Execute a real web search.

        Uses DuckDuckGo (no API key needed) as the default backend.
        Override this method or inject a different backend for production.
        """
        try:
            from duckduckgo_search import DDGS

            results: list[dict[str, Any]] = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=num_results):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })

            if results:
                summary_parts = [f"搜索查询: {query}\n"]
                for i, r in enumerate(results, 1):
                    summary_parts.append(f"{i}. {r['title']}\n   {r['snippet'][:200]}")
                summary = "\n".join(summary_parts)
            else:
                summary = f"未找到与 '{query}' 相关的搜索结果。"

            return {"summary": summary, "results": results, "query": query}
        except ImportError:
            # Fallback: return a note that web search is not configured
            logger.warning("duckduckgo_search not installed — returning mock result")
            return {
                "summary": f"[web_search 未配置] 请在服务器上安装 duckduckgo_search 包以启用网络搜索。查询：{query}",
                "results": [],
                "query": query,
            }
