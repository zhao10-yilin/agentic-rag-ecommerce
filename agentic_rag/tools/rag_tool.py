"""RAG tool — wraps the existing ``pdf_parser.rag.RAGQueryEngine``.

Provides two tools:
* ``rag_search`` — single-turn knowledge-base search.
* ``rag_chat`` — multi-turn conversation-aware search.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure pdf_parser is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agentic_rag.models import DegradationPolicy, ToolCall, ToolResult
from agentic_rag.tools.base import BaseTool

logger = logging.getLogger(__name__)

RAG_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query or question to look up in the knowledge base.",
        },
        "top_k": {
            "type": "integer",
            "description": "Number of source chunks to retrieve (default 10, max 20).",
            "default": 10,
        },
    },
    "required": ["query"],
}


RAG_CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The user message to send to the multi-turn chat engine.",
        },
        "session_id": {
            "type": "string",
            "description": "Conversation session ID. Omit to create a new session.",
        },
    },
    "required": ["query"],
}


class RAGSearchTool(BaseTool):
    """Search the e-commerce knowledge base for products, policies, guides.

    Wraps ``RAGQueryEngine.query()`` — single-turn, no conversation history.
    """

    def __init__(self) -> None:
        super().__init__(
            name="rag_search",
            description=(
                "搜索电商知识库，获取产品信息、购买指南、退换货政策、使用说明等。"
                "当你需要查找具体的产品信息、公司政策、操作指南时使用此工具。"
            ),
            parameters=RAG_SEARCH_SCHEMA,
            degradation=DegradationPolicy.SKIP,
        )
        self._engine: Any = None
        self._session_manager: Any = None

    async def execute(self, call: ToolCall) -> ToolResult:
        t0 = time.perf_counter()
        try:
            engine = self._get_engine()
            query = call.input.get("query", "")
            top_k = min(call.input.get("top_k", 10), 20)

            response = await engine.query(query)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            sources = [
                {
                    "chunk_id": s.chunk.chunk_id,
                    "file_id": s.chunk.file_id,
                    "text": s.chunk.text[:500],
                    "heading_path": s.chunk.heading_path,
                    "score": round(s.final_score, 4),
                }
                for s in response.sources[:top_k]
            ]

            return ToolResult(
                tool_name=self.name,
                status="success",
                summary=response.answer[:500],
                structured_data={
                    "answer": response.answer,
                    "sources": sources,
                    "needs_retrieval": response.query_plan.needs_retrieval if response.query_plan else True,
                    "strategy": response.query_plan.strategy if response.query_plan else "retrieve",
                },
                elapsed_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.exception("RAG search failed for query: %s", call.input.get("query", "")[:80])
            return ToolResult(
                tool_name=self.name,
                status="error",
                summary=f"知识库搜索失败：{exc}",
                structured_data={},
                error=str(exc),
                error_code="RAG_SEARCH_ERROR",
                elapsed_ms=round(elapsed_ms, 2),
            )

    def _get_engine(self) -> Any:
        if self._engine is None:
            from pdf_parser.rag.embedder import EmbeddingService, SentenceTransformerEmbedder
            from pdf_parser.rag.llm_gateway import LLMGateway
            from pdf_parser.rag.query_engine import RAGQueryEngine
            from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore

            from agentic_rag.config import get_settings

            settings = get_settings()

            embedder = EmbeddingService(SentenceTransformerEmbedder())
            dense = ChromaVectorStore(
                persist_directory=settings.chroma_persist_dir,
            )
            sparse = SQLiteFTSStore(db_path=settings.fts_db_path)

            try:
                llm = LLMGateway(
                    model=settings.llm_model,
                    light_model=settings.llm_light_model_effective,
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url,
                )
            except ValueError:
                logger.warning("LLMGateway not available — RAG search will fail without LLM")
                llm = None

            self._engine = RAGQueryEngine(
                dense_store=dense,
                sparse_store=sparse,
                embedder=embedder,
                llm=llm,
            )
        return self._engine


class RAGChatTool(BaseTool):
    """Multi-turn conversation-aware RAG search.

    Wraps ``RAGQueryEngine.chat()`` with session management.
    """

    def __init__(self) -> None:
        super().__init__(
            name="rag_chat",
            description=(
                "在电商知识库中进行多轮对话式搜索。适合需要结合对话历史理解的追问场景。"
                "需要提供 session_id 来维持会话上下文。"
            ),
            parameters=RAG_CHAT_SCHEMA,
            degradation=DegradationPolicy.SKIP,
        )
        self._engine: Any = None
        self._session_manager: Any = None

    async def execute(self, call: ToolCall) -> ToolResult:
        t0 = time.perf_counter()
        try:
            engine = self._get_engine()
            session_mgr = self._get_session_manager()
            query = call.input.get("query", "")
            session_id = call.input.get("session_id")

            response = await engine.chat(query, session_mgr, session_id=session_id)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            return ToolResult(
                tool_name=self.name,
                status="success",
                summary=response.answer[:500],
                structured_data={
                    "answer": response.answer,
                    "session_id": response.session_id,
                    "sources": response.sources,
                    "turn_number": response.turn_number,
                },
                elapsed_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.exception("RAG chat failed for query: %s", call.input.get("query", "")[:80])
            return ToolResult(
                tool_name=self.name,
                status="error",
                summary=f"多轮对话搜索失败：{exc}",
                structured_data={},
                error=str(exc),
                error_code="RAG_CHAT_ERROR",
                elapsed_ms=round(elapsed_ms, 2),
            )

    def _get_engine(self) -> Any:
        if self._engine is None:
            from pdf_parser.rag.embedder import EmbeddingService, SentenceTransformerEmbedder
            from pdf_parser.rag.llm_gateway import LLMGateway
            from pdf_parser.rag.query_engine import RAGQueryEngine
            from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore

            from agentic_rag.config import get_settings

            settings = get_settings()

            embedder = EmbeddingService(SentenceTransformerEmbedder())
            dense = ChromaVectorStore(persist_directory=settings.chroma_persist_dir)
            sparse = SQLiteFTSStore(db_path=settings.fts_db_path)

            try:
                llm = LLMGateway(
                    model=settings.llm_model,
                    light_model=settings.llm_light_model_effective,
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url,
                )
            except ValueError:
                llm = None

            self._engine = RAGQueryEngine(
                dense_store=dense,
                sparse_store=sparse,
                embedder=embedder,
                llm=llm,
            )
        return self._engine

    def _get_session_manager(self) -> Any:
        if self._session_manager is None:
            from pdf_parser.rag.session import SessionManager

            self._session_manager = SessionManager()
        return self._session_manager
