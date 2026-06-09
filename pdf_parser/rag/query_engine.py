"""RAG query engine — orchestrates the full retrieve-then-read pipeline.

This is the top-level entry point that application code calls.  It strings
together:

1. **Query analysis** (LLM → :class:`QueryPlan`) — Self-RAG decision + rewriting.
2. **Hybrid retrieval** (:class:`HybridRetriever`) — dense + sparse → RRF.
3. **Re-ranking** (:class:`Reranker`) — cross-encoder refinement.
4. **Context assembly** — small-to-big chunk expansion via parent IDs.
5. **Answer generation** (LLM) — grounded response with citations.

Single-turn usage::

    engine = RAGQueryEngine(...)
    response = await engine.query("违约责任的构成要件是什么？")

Multi-turn chat usage::

    engine = RAGQueryEngine(...)
    session_mgr = SessionManager()

    # Turn 1
    resp1 = await engine.chat("什么是违约责任", session_mgr, session_id=None)
    # resp1.session_id = "abc123"

    # Turn 2 — the engine resolves "那第二条呢" using conversation history
    resp2 = await engine.chat("那第二条呢", session_mgr, session_id="abc123")
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pdf_parser.rag.llm_gateway import LLMGateway
from pdf_parser.rag.models import (
    ChatResponse,
    DocumentChunk,
    QueryPlan,
    RAGResponse,
    RetrievalResult,
)
from pdf_parser.rag.retriever import HybridRetriever, Reranker
from pdf_parser.rag.session import ChatSession, SessionManager
from pdf_parser.rag.vector_store import BaseVectorStore

logger = logging.getLogger(__name__)


class RAGQueryEngine:
    """End-to-end RAG query pipeline.

    Parameters
    ----------
    dense_store:
        Vector index for semantic (dense) search.
    sparse_store:
        Full-text index for keyword (sparse) search.
    embedder:
        Service with an ``embed_query(text) -> list[float]`` method.
    llm:
        :class:`LLMGateway` for query analysis and answer generation.
    reranker:
        Cross-encoder reranker.  Created automatically if ``None``.
    hybrid_top_k:
        Number of candidates to fetch in the hybrid retrieval stage.
    rerank_top_n:
        Number of candidates to keep after re-ranking.
    """

    def __init__(
        self,
        dense_store: BaseVectorStore,
        sparse_store: Any,  # SQLiteFTSStore
        embedder: Any,  # EmbeddingService
        llm: LLMGateway,
        *,
        reranker: Reranker | None = None,
        hybrid_top_k: int = 100,
        rerank_top_n: int = 10,
    ) -> None:
        self._llm = llm

        self._retriever = HybridRetriever(
            dense_store=dense_store,
            sparse_store=sparse_store,
            embed_query_fn=embedder.embed_query,
        )

        self._reranker = reranker or Reranker()
        self._dense = dense_store
        self._hybrid_top_k = hybrid_top_k
        self._rerank_top_n = rerank_top_n

    # ------------------------------------------------------------------
    # Single-turn query
    # ------------------------------------------------------------------

    async def query(self, user_query: str) -> RAGResponse:
        """Run the full RAG pipeline for a single user query.

        Returns a :class:`RAGResponse` with the answer, sources, and query plan.
        """
        t0 = time.perf_counter()

        # ---- 1. Query analysis (Self-RAG decision + rewriting) ----------
        plan = await self._llm.rewrite_query_async(user_query)

        if not plan.needs_retrieval:
            answer = await self._llm.generate_answer_async(user_query, [])
            elapsed = time.perf_counter() - t0
            return RAGResponse(
                answer=answer,
                sources=[],
                query_plan=plan,
                elapsed_seconds=round(elapsed, 3),
            )

        # ---- 2. Hybrid retrieval ----------------------------------------
        candidates = self._retriever.retrieve(
            plan,
            top_k=self._hybrid_top_k,
            filters={"chunk_level": "small"},
        )

        if not candidates:
            answer = "根据现有资料，我无法回答这个问题。"
            elapsed = time.perf_counter() - t0
            return RAGResponse(
                answer=answer,
                sources=[],
                query_plan=plan,
                elapsed_seconds=round(elapsed, 3),
            )

        # ---- 3. Re-ranking ----------------------------------------------
        ranked = self._reranker.rerank(
            user_query,
            candidates,
            top_n=self._rerank_top_n,
        )

        # ---- 4. Context assembly (small-to-big) -------------------------
        contexts = self._build_contexts(ranked)

        # ---- 5. Answer generation ---------------------------------------
        answer = await self._llm.generate_answer_async(user_query, contexts)

        elapsed = time.perf_counter() - t0
        logger.info(
            "RAG query complete in %.2fs: '%s' → %d sources, %d chars answer",
            elapsed,
            user_query[:60],
            len(ranked),
            len(answer),
        )

        return RAGResponse(
            answer=answer,
            sources=ranked,
            query_plan=plan,
            elapsed_seconds=round(elapsed, 3),
        )

    # ------------------------------------------------------------------
    # Multi-turn chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        user_message: str,
        session_manager: SessionManager,
        *,
        session_id: str | None = None,
    ) -> ChatResponse:
        """Process a message in a multi-turn conversation.

        Parameters
        ----------
        user_message:
            The user's latest message.
        session_manager:
            A :class:`SessionManager` that tracks conversation state.
        session_id:
            Existing session ID to continue a conversation.  ``None`` to
            start a new one.

        Returns:
            A :class:`ChatResponse` with the answer, sources, and session_id
            (use this session_id for the next turn).
        """
        t0 = time.perf_counter()

        session = session_manager.get_or_create(session_id)
        history = session.format_for_llm()

        # ---- 1. Query analysis with conversation context ----------------
        plan = await self._llm.rewrite_query_async(
            user_message,
            history=history,
        )

        # Use the first rewritten query for retrieval (it resolves anaphora)
        effective_query = plan.rewritten_queries[0] if plan.rewritten_queries else user_message

        # ---- 2. Record user message -------------------------------------
        session.add_turn(role="user", content=user_message)

        if not plan.needs_retrieval:
            answer = await self._llm.generate_chat_answer_async(
                user_message, [],
                history=history,
            )
            session.add_turn(role="assistant", content=answer)
            elapsed = time.perf_counter() - t0
            return ChatResponse(
                session_id=session.session_id,
                answer=answer,
                sources=[],
                turn_number=session.turn_count,
                query_plan={
                    "original_query": user_message,
                    "rewritten_queries": plan.rewritten_queries,
                    "needs_retrieval": False,
                    "strategy": plan.strategy,
                },
                elapsed_seconds=round(elapsed, 3),
            )

        # ---- 3. Hybrid retrieval ----------------------------------------
        # Use effective_query (rewritten) for retrieval
        retrieval_plan = QueryPlan(
            original_query=effective_query,
            rewritten_queries=plan.rewritten_queries[1:] if len(plan.rewritten_queries) > 1 else [],
            hyde_doc=plan.hyde_doc,
        )

        candidates = self._retriever.retrieve(
            retrieval_plan,
            top_k=self._hybrid_top_k,
            filters={"chunk_level": "small"},
        )

        if not candidates:
            answer = "根据现有资料，我无法回答这个问题。"
            session.add_turn(role="assistant", content=answer)
            elapsed = time.perf_counter() - t0
            return ChatResponse(
                session_id=session.session_id,
                answer=answer,
                sources=[],
                turn_number=session.turn_count,
                query_plan={
                    "original_query": user_message,
                    "rewritten_to": effective_query,
                    "needs_retrieval": True,
                    "strategy": "retrieve",
                },
                elapsed_seconds=round(elapsed, 3),
            )

        # ---- 4. Re-ranking ----------------------------------------------
        ranked = self._reranker.rerank(
            effective_query,
            candidates,
            top_n=self._rerank_top_n,
        )

        # ---- 5. Context assembly ----------------------------------------
        contexts = self._build_contexts(ranked)
        session.track_chunks([r.chunk.chunk_id for r in ranked])

        # ---- 6. Answer generation with conversation history -------------
        answer = await self._llm.generate_chat_answer_async(
            effective_query,
            contexts,
            history=history,
        )
        session.add_turn(
            role="assistant",
            content=answer,
            sources=[
                {
                    "chunk_id": r.chunk.chunk_id,
                    "file_id": r.chunk.file_id,
                    "text": r.chunk.text[:300],
                    "heading_path": r.chunk.heading_path,
                    "score": round(r.final_score, 4),
                }
                for r in ranked
            ],
        )

        elapsed = time.perf_counter() - t0
        logger.info(
            "Chat turn %d complete in %.2fs: '%s' → '%s' → %d sources",
            session.turn_count,
            elapsed,
            user_message[:40],
            effective_query[:40],
            len(ranked),
        )

        return ChatResponse(
            session_id=session.session_id,
            answer=answer,
            sources=[
                {
                    "chunk_id": r.chunk.chunk_id,
                    "file_id": r.chunk.file_id,
                    "text": r.chunk.text[:500],
                    "heading_path": r.chunk.heading_path,
                    "score": round(r.final_score, 4),
                }
                for r in ranked
            ],
            turn_number=session.turn_count,
            query_plan={
                "original_query": user_message,
                "rewritten_to": effective_query,
                "needs_retrieval": plan.needs_retrieval,
                "strategy": plan.strategy,
            },
            elapsed_seconds=round(elapsed, 3),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_contexts(self, ranked: list[RetrievalResult]) -> list[str]:
        """Expand small chunks to their parent big chunks for LLM context.

        Deduplicates by parent_chunk_id so the LLM doesn't see the same
        section twice.
        """
        seen_parents: set[str] = set()
        contexts: list[str] = []

        for result in ranked:
            chunk = result.chunk

            if chunk.parent_chunk_id:
                if chunk.parent_chunk_id in seen_parents:
                    continue
                seen_parents.add(chunk.parent_chunk_id)

                parent = self._dense.get_by_chunk_id(chunk.parent_chunk_id)
                if parent and parent.text:
                    contexts.append(self._format_context(parent, result.final_score))
                    continue

            # No parent or parent lookup failed — use the small chunk directly
            if chunk.chunk_id not in seen_parents:
                seen_parents.add(chunk.chunk_id)
                contexts.append(self._format_context(chunk, result.final_score))

        return contexts

    @staticmethod
    def _format_context(chunk: DocumentChunk, score: float) -> str:
        """Format a chunk as context for the LLM, with heading breadcrumbs."""
        if chunk.heading_path:
            heading = " > ".join(chunk.heading_path)
            return f"[来源: {heading} | 相关度: {score:.2f}]\n{chunk.text}"
        return chunk.text
