"""Celery tasks for asynchronous RAG indexing.

Usage::

    celery -A pdf_parser.rag.tasks worker -c 2 --loglevel=info
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import Celery
from celery.utils.log import get_task_logger

from pdf_parser.rag.embedder import EmbeddingService, SentenceTransformerEmbedder
from pdf_parser.rag.indexer import RAGIndexer
from pdf_parser.rag.vector_store import ChromaVectorStore, SQLiteFTSStore

logger = get_task_logger(__name__)

# Reuse the main Celery app — worker processes share the config
from pdf_parser.tasks import app  # noqa: E402, F401

# ---------------------------------------------------------------------------
# Lazy-loaded RAG infrastructure (heavy models — load once per worker)
# ---------------------------------------------------------------------------

_embedder: EmbeddingService | None = None
_dense_store: ChromaVectorStore | None = None
_sparse_store: SQLiteFTSStore | None = None
_indexer: RAGIndexer | None = None


def _get_indexer() -> RAGIndexer:
    """Return a process-level singleton :class:`RAGIndexer`.

    Embedding models are expensive to load, so each Celery worker process
    creates them once and reuses them for the lifetime of the process.
    """
    global _embedder, _dense_store, _sparse_store, _indexer

    if _indexer is None:
        _embedder = EmbeddingService(SentenceTransformerEmbedder())
        _dense_store = ChromaVectorStore()
        _sparse_store = SQLiteFTSStore()
        _indexer = RAGIndexer(
            dense_store=_dense_store,
            sparse_store=_sparse_store,
            embedder=_embedder,
        )
        logger.info("RAGIndexer initialised in worker process")
    return _indexer


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


@app.task(bind=True, max_retries=2)
def index_pdf_task(
    self,
    file_path: str,
    *,
    file_id: str | None = None,
    parser_config: dict[str, Any] | None = None,
    cleaning_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse a PDF **and** index it into the RAG stores in one shot.

    This task composes :func:`parse_pdf_task` with :class:`RAGIndexer`.
    It is the recommended way to ingest new documents into the RAG system
    because it guarantees that the parse + index pipeline runs atomically
    inside a single worker.

    Args:
        file_path: Absolute path to the PDF file.
        file_id: Optional identifier; falls back to the file stem.
        parser_config: Forwarded to :class:`MinerUParser`.
        cleaning_config: Forwarded to :class:`TextCleaner`.

    Returns:
        Dict with ``file_id``, ``chunk_count``, and ``status``.
    """
    from pdf_parser.cleaning import TextCleaner
    from pdf_parser.models import ParseResult, Status
    from pdf_parser.strategies import MinerUParser

    path = Path(file_path)
    resolved_id = file_id or path.stem

    logger.info("Starting index task for %s", resolved_id)

    try:
        # 1. Parse
        parser = MinerUParser(config=(parser_config or {}))
        result: ParseResult = parser.parse(path, file_id=resolved_id)

        # 2. Clean
        if result.status == Status.SUCCESS and cleaning_config:
            cleaner = TextCleaner(config=cleaning_config)
            cleaned = cleaner.clean(result.markdown_content)
            if cleaned != result.markdown_content:
                result = result.model_copy(update={"cleaned_markdown": cleaned})

        if result.status != Status.SUCCESS:
            return {
                "file_id": resolved_id,
                "status": "parse_failed",
                "error": result.error_msg,
            }

        # 3. Index
        indexer = _get_indexer()
        chunks = indexer.index_parse_result(result)

        return {
            "file_id": resolved_id,
            "status": "indexed",
            "chunk_count": len(chunks),
            "small_chunks": sum(1 for c in chunks if c.chunk_level == "small"),
            "big_chunks": sum(1 for c in chunks if c.chunk_level == "big"),
        }

    except Exception as exc:
        logger.exception("Index task failed for %s", resolved_id)
        raise self.retry(exc=exc, countdown=30)
