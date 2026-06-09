"""FastAPI routes for RAG indexing and querying.

Mount these on the main FastAPI app::

    from pdf_parser.rag.api import router as rag_router
    app.include_router(rag_router, prefix="/rag")
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from pdf_parser.rag.embedder import EmbeddingService, SentenceTransformerEmbedder
from pdf_parser.rag.retriever import Reranker
from pdf_parser.rag.llm_gateway import LLMGateway
from pdf_parser.rag.query_engine import RAGQueryEngine
from pdf_parser.rag.session import SessionManager
from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RAG"])

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class IndexRequest(BaseModel):
    """Request to index a PDF into the RAG stores."""

    file_path: str = Field(..., description="Server-side path to the PDF file.")
    file_id: str | None = Field(
        None,
        description="Document identifier.  Defaults to the file stem.",
    )
    enable_ocr: bool = Field(True)
    enable_cleaning: bool = Field(True)


class IndexResponse(BaseModel):
    file_id: str
    status: str
    chunk_count: int = 0
    small_chunks: int = 0
    big_chunks: int = 0
    error: str | None = None


class QueryRequest(BaseModel):
    """Request to query the RAG engine."""

    query: str = Field(..., description="The user's question.")
    top_k: int = Field(10, ge=1, le=50, description="Number of sources to return.")
    rerank: bool = Field(True, description="Enable cross-encoder re-ranking.")


class SourceItem(BaseModel):
    chunk_id: str
    file_id: str
    text: str
    heading_path: list[str]
    score: float

    model_config = {"frozen": True}


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceItem]
    needs_retrieval: bool
    strategy: str
    elapsed_seconds: float


class StatsResponse(BaseModel):
    dense_chunks: int
    sparse_chunks: int
    embedder: str


class DeleteResponse(BaseModel):
    file_id: str
    dense_deleted: int
    sparse_deleted: int


class ChatRequest(BaseModel):
    """Request for multi-turn chat."""

    message: str = Field(..., description="The user's latest message.")
    session_id: str | None = Field(
        None,
        description="Existing session ID to continue a conversation. "
                    "Omit to start a new one.",
    )


class ChatResponseSchema(BaseModel):
    session_id: str
    answer: str
    sources: list[SourceItem]
    turn_number: int
    rewritten_to: str | None = None
    needs_retrieval: bool = True
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Lazy engine singleton
# ---------------------------------------------------------------------------

_engine: RAGQueryEngine | None = None
_embedder: EmbeddingService | None = None
_dense_store: ChromaVectorStore | None = None
_sparse_store: SQLiteFTSStore | None = None
_session_manager: SessionManager = SessionManager()


def _get_engine() -> RAGQueryEngine:
    """Return a process-level singleton :class:`RAGQueryEngine`.

    All heavy models (embedding, reranker) are loaded once and reused.
    """
    global _engine, _embedder, _dense_store, _sparse_store

    if _engine is None:
        logger.info("Initialising RAG query engine (loading models)...")

        _embedder = EmbeddingService(SentenceTransformerEmbedder())

        _dense_store = ChromaVectorStore()
        _sparse_store = SQLiteFTSStore()

        try:
            llm = LLMGateway()
        except ValueError:
            logger.warning(
                "LLMGateway could not be initialised — set DEEPSEEK_API_KEY "
                "or OPENAI_API_KEY. Query rewriting and answer generation "
                "will be unavailable."
            )
            llm = None  # type: ignore[assignment]

        _engine = RAGQueryEngine(
            dense_store=_dense_store,
            sparse_store=_sparse_store,
            embedder=_embedder,
            llm=llm,  # type: ignore[arg-type]
        )

        logger.info("RAG query engine ready")
    return _engine


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/index", response_model=IndexResponse)
def index_document(body: IndexRequest) -> dict[str, Any]:
    """Index a server-side PDF file into the RAG stores synchronously.

    For production use with large files prefer the async Celery task
    (``POST /parse`` followed by Celery auto-index) to avoid blocking
    the API worker.
    """
    from pdf_parser.cleaning import TextCleaner
    from pdf_parser.models import Status
    from pdf_parser.strategies import MinerUParser

    from pdf_parser.rag.indexer import RAGIndexer

    path = body.file_path
    resolved_id = body.file_id or os.path.splitext(os.path.basename(path))[0]

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        # Parse
        parser = MinerUParser(
            config={
                "enable_ocr": body.enable_ocr,
                "extract_images": True,
            }
        )
        result = parser.parse(path, file_id=resolved_id)

        # Clean
        if result.status == Status.SUCCESS and body.enable_cleaning:
            cleaner = TextCleaner()
            cleaned = cleaner.clean(result.markdown_content)
            if cleaned != result.markdown_content:
                result = result.model_copy(update={"cleaned_markdown": cleaned})

        if result.status != Status.SUCCESS:
            return {
                "file_id": resolved_id,
                "status": "parse_failed",
                "error": result.error_msg,
            }

        # Index
        engine = _get_engine()
        embedder = _embedder
        dense_store = _dense_store
        sparse_store = _sparse_store

        if embedder is None or dense_store is None or sparse_store is None:
            raise HTTPException(status_code=500, detail="RAG infrastructure not initialised")

        indexer = RAGIndexer(
            dense_store=dense_store,
            sparse_store=sparse_store,
            embedder=embedder,
        )
        chunks = indexer.index_parse_result(result)

        return {
            "file_id": resolved_id,
            "status": "indexed",
            "chunk_count": len(chunks),
            "small_chunks": sum(1 for c in chunks if c.chunk_level == "small"),
            "big_chunks": sum(1 for c in chunks if c.chunk_level == "big"),
        }

    except Exception as exc:
        logger.exception("Index failed for %s", resolved_id)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/query", response_model=QueryResponse)
async def query_rag(body: QueryRequest) -> dict[str, Any]:
    """Query the RAG engine and return a grounded answer with sources."""
    import asyncio

    engine = _get_engine()

    try:
        response = await engine.query(body.query)
    except Exception as exc:
        logger.exception("RAG query failed")
        raise HTTPException(status_code=500, detail=str(exc))

    sources: list[dict[str, Any]] = []
    for src in response.sources[: body.top_k]:
        sources.append(
            {
                "chunk_id": src.chunk.chunk_id,
                "file_id": src.chunk.file_id,
                "text": src.chunk.text[:500],  # truncate for API response
                "heading_path": src.chunk.heading_path,
                "score": round(src.final_score, 4),
            }
        )

    return {
        "answer": response.answer,
        "sources": sources,
        "needs_retrieval": response.query_plan.needs_retrieval if response.query_plan else True,
        "strategy": response.query_plan.strategy if response.query_plan else "retrieve",
        "elapsed_seconds": response.elapsed_seconds,
    }


@router.delete("/index/{file_id}", response_model=DeleteResponse)
def delete_index(file_id: str) -> dict[str, Any]:
    """Remove all indexed chunks for a document."""
    dense_store = _dense_store
    sparse_store = _sparse_store

    if dense_store is None or sparse_store is None:
        raise HTTPException(status_code=500, detail="RAG stores not initialised")

    d = dense_store.delete_by_file_id(file_id)
    s = sparse_store.delete_by_file_id(file_id)
    return {"file_id": file_id, "dense_deleted": d, "sparse_deleted": s}


@router.post("/chat", response_model=ChatResponseSchema)
async def chat_rag(body: ChatRequest) -> dict[str, Any]:
    """Multi-turn RAG conversation endpoint.

    Send a message and receive a grounded answer with sources.
    Pass the returned ``session_id`` in subsequent requests to maintain
    conversation context.

    Example::

        # Turn 1 — start a new conversation
        curl -X POST .../rag/chat -d '{"message": "什么是违约责任"}'
        # → {"session_id": "abc123", "answer": "...", "turn_number": 1}

        # Turn 2 — continue (LLM resolves "那第二条呢" via history)
        curl -X POST .../rag/chat -d '{"message": "那第二条呢", "session_id": "abc123"}'
        # → {"session_id": "abc123", "answer": "...", "turn_number": 2}
    """
    engine = _get_engine()

    try:
        response = await engine.chat(
            body.message,
            _session_manager,
            session_id=body.session_id,
        )
    except Exception as exc:
        logger.exception("RAG chat failed")
        raise HTTPException(status_code=500, detail=str(exc))

    sources: list[dict[str, Any]] = []
    for src in response.sources[:10]:
        sources.append(
            {
                "chunk_id": src["chunk_id"],
                "file_id": src["file_id"],
                "text": src["text"][:500],
                "heading_path": src["heading_path"],
                "score": src["score"],
            }
        )

    return {
        "session_id": response.session_id,
        "answer": response.answer,
        "sources": sources,
        "turn_number": response.turn_number,
        "rewritten_to": (
            response.query_plan.get("rewritten_to")
            if response.query_plan
            else None
        ),
        "needs_retrieval": (
            response.query_plan.get("needs_retrieval", True)
            if response.query_plan
            else True
        ),
        "elapsed_seconds": response.elapsed_seconds,
    }


@router.get("/stats", response_model=StatsResponse)
def get_stats() -> dict[str, Any]:
    """Return index statistics."""
    dense_store = _dense_store
    sparse_store = _sparse_store
    embedder = _embedder

    return {
        "dense_chunks": dense_store.count() if dense_store else 0,
        "sparse_chunks": sparse_store.count() if sparse_store else 0,
        "embedder": type(embedder._provider).__name__ if embedder else "not_loaded",
    }
