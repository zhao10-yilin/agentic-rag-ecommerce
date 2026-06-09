"""Indexing pipeline — from :class:`ParseResult` to searchable vector store.

The :class:`RAGIndexer` is the bridge between the PDF parsing subsystem and
the RAG retrieval subsystem.  It takes a :class:`ParseResult`, chunks the
Markdown, generates embeddings, and writes everything to the dense + sparse
indexes.

Key design properties
---------------------
* **Idempotent** — re-indexing the same ``file_id`` deletes old chunks
  before inserting new ones, so caller never needs to clean up manually.
* **Incremental** — the caller can query ``is_indexed(file_id)`` before
  submitting work.
* **Pluggable stores** — any :class:`BaseVectorStore` + :class:`SQLiteFTSStore`
  pair works.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pdf_parser.models import ParseResult, Status
from pdf_parser.rag.chunker import SemanticChunker
from pdf_parser.rag.embedder import EmbeddingService
from pdf_parser.rag.models import DocumentChunk
from pdf_parser.rag.vector_store import (
    BaseVectorStore,
    SQLiteFTSStore,
)

logger = logging.getLogger(__name__)


class RAGIndexer:
    """Ingest :class:`ParseResult` objects into the vector + keyword stores.

    Parameters
    ----------
    dense_store:
        Vector index backend (e.g. :class:`ChromaVectorStore`).
    sparse_store:
        Full-text index backend (:class:`SQLiteFTSStore`).
    embedder:
        Embedding service for generating dense vectors.
    chunker:
        Semantic chunker.  Created with sensible defaults if ``None``.
    """

    def __init__(
        self,
        dense_store: BaseVectorStore,
        sparse_store: SQLiteFTSStore,
        embedder: EmbeddingService,
        *,
        chunker: SemanticChunker | None = None,
    ) -> None:
        self._dense = dense_store
        self._sparse = sparse_store
        self._embedder = embedder

        self._chunker = chunker or SemanticChunker(
            embed_fn=embedder.embed_documents,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_parse_result(
        self,
        result: ParseResult,
    ) -> list[DocumentChunk]:
        """Index a :class:`ParseResult` into both stores.

        Only successful parses with content are indexed.  The caller should
        ensure this is called after the cleaning pipeline has populated
        ``cleaned_markdown``.

        Returns the list of all chunks created (both small and big).
        """
        if result.status != Status.SUCCESS:
            logger.warning("Skipping index of failed parse: %s", result.file_id)
            return []

        text = result.cleaned_markdown or result.markdown_content
        if not text.strip():
            logger.warning("Skipping index of empty content: %s", result.file_id)
            return []

        # Idempotency: remove old chunks first
        self.delete_by_file_id(result.file_id)

        # Chunk
        chunks = self._chunker.chunk(text, file_id=result.file_id)
        if not chunks:
            return []

        # Generate embeddings for small chunks only (big chunks are for context)
        small_chunks = [c for c in chunks if c.chunk_level == "small"]
        small_texts = [c.text for c in small_chunks]

        if small_texts:
            logger.info("Generating embeddings for %d small chunks...", len(small_texts))
            embeddings = self._embedder.embed_documents(small_texts)

            # Attach embeddings to small chunks (create new frozen objects)
            embedded_smalls: list[DocumentChunk] = []
            for chunk, emb in zip(small_chunks, embeddings):
                embedded_smalls.append(
                    DocumentChunk(
                        chunk_id=chunk.chunk_id,
                        file_id=chunk.file_id,
                        text=chunk.text,
                        heading_path=chunk.heading_path,
                        chunk_level=chunk.chunk_level,
                        parent_chunk_id=chunk.parent_chunk_id,
                        page_number=chunk.page_number,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        metadata=chunk.metadata,
                        embedding=emb,
                    )
                )

            # Rebuild chunk list with embedded smalls + original bigs
            big_chunks = [c for c in chunks if c.chunk_level == "big"]
            all_chunks = embedded_smalls + big_chunks
        else:
            all_chunks = chunks

        # Write to both stores
        self._dense.upsert(all_chunks)
        self._sparse.upsert(all_chunks)

        logger.info(
            "Indexed %s: %d chunks (%d small, %d big) → dense + sparse stores",
            result.file_id,
            len(all_chunks),
            sum(1 for c in all_chunks if c.chunk_level == "small"),
            sum(1 for c in all_chunks if c.chunk_level == "big"),
        )
        return all_chunks

    def index_text(
        self,
        text: str,
        *,
        file_id: str,
    ) -> list[DocumentChunk]:
        """Index a raw text string directly (bypasses PDF parsing).

        Useful for testing or for indexing non-PDF content through the
        same pipeline.
        """
        # Idempotency
        self.delete_by_file_id(file_id)

        chunks = self._chunker.chunk(text, file_id=file_id)
        if not chunks:
            return []

        small_chunks = [c for c in chunks if c.chunk_level == "small"]
        small_texts = [c.text for c in small_chunks]

        if small_texts:
            embeddings = self._embedder.embed_documents(small_texts)
            embedded_smalls: list[DocumentChunk] = []
            for chunk, emb in zip(small_chunks, embeddings):
                embedded_smalls.append(
                    DocumentChunk(
                        chunk_id=chunk.chunk_id,
                        file_id=chunk.file_id,
                        text=chunk.text,
                        heading_path=chunk.heading_path,
                        chunk_level=chunk.chunk_level,
                        parent_chunk_id=chunk.parent_chunk_id,
                        page_number=chunk.page_number,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        metadata=chunk.metadata,
                        embedding=emb,
                    )
                )
            big_chunks = [c for c in chunks if c.chunk_level == "big"]
            all_chunks = embedded_smalls + big_chunks
        else:
            all_chunks = chunks

        self._dense.upsert(all_chunks)
        self._sparse.upsert(all_chunks)
        return all_chunks

    def delete_by_file_id(self, file_id: str) -> tuple[int, int]:
        """Remove all chunks for *file_id* from both stores.

        Returns ``(dense_deleted, sparse_deleted)``.
        """
        d = self._dense.delete_by_file_id(file_id)
        s = self._sparse.delete_by_file_id(file_id)
        return d, s

    def is_indexed(self, file_id: str) -> bool:
        """Check whether *file_id* has any chunks in the sparse store.

        Uses a direct ``chunk_meta`` query (O(1) with index), not a dummy
        embedding HNSW search.
        """
        return self._sparse.is_indexed(file_id)
