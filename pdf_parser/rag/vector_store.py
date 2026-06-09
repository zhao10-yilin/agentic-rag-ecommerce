"""Vector store abstraction with ChromaDB (dense) and SQLite FTS5 (sparse).

Provides two complementary indexes that the :class:`HybridRetriever` fuses
via Reciprocal Rank Fusion (RRF):

``ChromaVectorStore``
    Dense semantic search using cosine similarity over L2-normalised
    embedding vectors.

``SQLiteFTSStore``
    Sparse keyword search using BM25 over a full-text index.  Built on
    SQLite's built-in FTS5 module — no external service required.

Both stores share a common abstract interface so that either can be
replaced independently.
"""

from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pdf_parser.rag.models import DocumentChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class BaseVectorStore(ABC):
    """Minimal vector-store interface that all backends must implement."""

    @abstractmethod
    def upsert(self, chunks: list[DocumentChunk]) -> None:
        """Insert or update *chunks* in the store."""
        ...

    @abstractmethod
    def dense_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Return ``(chunk_id, score)`` for the top-*k* matches.

        *filters* is an optional dict of ``{metadata_field: value}`` pairs
        that must all match (AND semantics).
        """
        ...

    @abstractmethod
    def delete_by_file_id(self, file_id: str) -> int:
        """Remove all chunks belonging to *file_id*.  Returns count deleted."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Total number of chunks currently stored."""
        ...


# ---------------------------------------------------------------------------
# ChromaDB — dense vector search
# ---------------------------------------------------------------------------


class ChromaVectorStore(BaseVectorStore):
    """Dense vector index backed by ChromaDB.

    Parameters
    ----------
    persist_dir:
        Directory where ChromaDB stores its data.  Created if missing.
    collection_name:
        Logical collection name.  Useful for multi-tenant / multi-project
        isolation.
    """

    def __init__(
        self,
        persist_dir: str | Path = "./chroma_data",
        *,
        collection_name: str = "pdf_rag",
    ) -> None:
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._collection_name = collection_name
        self._client: Any = None
        self._collection: Any = None

    # ------------------------------------------------------------------
    # Lazy init
    # ------------------------------------------------------------------

    def _ensure_client(self) -> None:
        if self._client is not None:
            return

        import chromadb

        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
        )
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ChromaDB collection '%s' ready (%d docs)",
            self._collection_name,
            self._collection.count(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        self._ensure_client()

        # Only upsert chunks that have embeddings (small chunks).
        # Big chunks are context-only and don't need to be in the dense index.
        embedded = [c for c in chunks if c.embedding and len(c.embedding) > 0]
        if not embedded:
            logger.debug("ChromaDB upsert skipped — no chunks with embeddings")
            return

        ids: list[str] = []
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for c in embedded:
            ids.append(c.chunk_id)
            embeddings.append(c.embedding or [])
            documents.append(c.text)
            metadatas.append(
                {
                    "file_id": c.file_id,
                    "chunk_level": c.chunk_level,
                    "heading_path": " > ".join(c.heading_path),
                    "parent_chunk_id": c.parent_chunk_id or "",
                }
            )

        # ChromaDB upsert: existing IDs are replaced
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.debug("ChromaDB upserted %d chunks", len(chunks))

    def dense_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        self._ensure_client()

        where: dict[str, Any] | None = None
        if filters:
            where = {}
            for k, v in filters.items():
                where[k] = v

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self._collection.count()),
            where=where,
            include=["distances"],
        )

        ids: list[str] = results.get("ids", [[]])[0] or []
        distances: list[float] = results.get("distances", [[]])[0] or []

        # ChromaDB returns cosine *distance* (1 - similarity) when
        # hnsw:space=cosine.  Convert to similarity score.
        out: list[tuple[str, float]] = []
        for chunk_id, dist in zip(ids, distances):
            score = 1.0 - dist
            out.append((chunk_id, score))
        return out

    def delete_by_file_id(self, file_id: str) -> int:
        self._ensure_client()

        # ChromaDB doesn't support delete-by-metadata natively in all versions,
        # so we query first then delete by ID.
        existing = self._collection.get(
            where={"file_id": file_id},
            include=[],
        )
        ids_to_delete = existing.get("ids", [])
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
            logger.info("ChromaDB deleted %d chunks for file_id=%s", len(ids_to_delete), file_id)
        return len(ids_to_delete)

    def count(self) -> int:
        self._ensure_client()
        return self._collection.count()

    def get_by_chunk_id(self, chunk_id: str) -> DocumentChunk | None:
        """Retrieve a single chunk by ID (used for parent-chunk lookup)."""
        self._ensure_client()
        result = self._collection.get(
            ids=[chunk_id],
            include=["documents", "metadatas"],
        )
        if not result["ids"]:
            return None
        meta = result["metadatas"][0]
        return DocumentChunk(
            chunk_id=chunk_id,
            file_id=meta.get("file_id", ""),
            text=result["documents"][0],
            heading_path=meta.get("heading_path", "").split(" > ") if meta.get("heading_path") else [],
            chunk_level=meta.get("chunk_level", "small"),
            parent_chunk_id=meta.get("parent_chunk_id") or None,
        )


# ---------------------------------------------------------------------------
# SQLite FTS5 — sparse keyword search
# ---------------------------------------------------------------------------


class SQLiteFTSStore:
    """Sparse / keyword index using SQLite's built-in FTS5 engine.

    FTS5 provides BM25 scoring out of the box and runs entirely in-process,
    so there is no separate service to manage.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created if missing.
    """

    def __init__(self, db_path: str | Path = "./fts_data/fts.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks
                USING fts5(
                    chunk_id,
                    file_id,
                    text,
                    heading_path
                )
                """
            )
            # Shadow table for metadata not in FTS index
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_meta (
                    chunk_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    chunk_level TEXT NOT NULL DEFAULT 'small',
                    parent_chunk_id TEXT,
                    heading_path TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_file ON chunk_meta(file_id)"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, chunks: list[DocumentChunk]) -> None:
        """Insert or replace chunks in the FTS index and metadata table."""
        if not chunks:
            return

        with self._connect() as conn:
            for c in chunks:
                heading = " > ".join(c.heading_path)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO fts_chunks(chunk_id, file_id, text, heading_path)
                    VALUES (?, ?, ?, ?)
                    """,
                    (c.chunk_id, c.file_id, c.text, heading),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO chunk_meta(chunk_id, file_id, chunk_level, parent_chunk_id, heading_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (c.chunk_id, c.file_id, c.chunk_level, c.parent_chunk_id or "", heading),
                )
        logger.debug("FTS5 upserted %d chunks", len(chunks))

    def sparse_search(
        self,
        query: str,
        *,
        top_k: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Full-text search using BM25 scoring.

        Returns ``(chunk_id, bm25_score)`` sorted by relevance descending.
        BM25 scores are inverted so that higher = better, normalised to [0, 1].
        """
        with self._connect() as conn:
            # FTS5 BM25 returns negative values for less relevant matches;
            # we negate so that higher = better.
            where_clauses: list[str] = []
            params: list[Any] = []

            if filters:
                for k, v in filters.items():
                    if k == "file_id":
                        where_clauses.append("cm.file_id = ?")
                        params.append(v)
                    elif k == "chunk_level":
                        where_clauses.append("cm.chunk_level = ?")
                        params.append(v)

            where_sql = ""
            if where_clauses:
                where_sql = "AND " + " AND ".join(where_clauses)

            # FTS5 query syntax: double-quote the user query for phrase matching
            # and escape special characters.
            safe_query = query.replace('"', '""')
            rows = conn.execute(
                f"""
                SELECT
                    f.chunk_id,
                    bm25(fts_chunks, 0.75, 0.0) AS score
                FROM fts_chunks f
                JOIN chunk_meta cm ON f.chunk_id = cm.chunk_id
                WHERE fts_chunks MATCH ?
                {where_sql}
                ORDER BY score
                LIMIT ?
                """,
                [f'"{safe_query}"', *params, top_k],
            ).fetchall()

            if not rows:
                return []

            # Normalise BM25: shift to [0, 1] range
            scores = [r["score"] for r in rows]
            min_s = min(scores)
            max_s = max(scores)
            span = max_s - min_s if max_s != min_s else 1

            results: list[tuple[str, float]] = []
            for r in rows:
                norm_score = (r["score"] - min_s) / span
                results.append((r["chunk_id"], norm_score))
            return results

    def delete_by_file_id(self, file_id: str) -> int:
        """Remove all chunks and metadata for *file_id*."""
        with self._connect() as conn:
            # Get chunk_ids first
            rows = conn.execute(
                "SELECT chunk_id FROM chunk_meta WHERE file_id = ?",
                (file_id,),
            ).fetchall()
            chunk_ids = [r["chunk_id"] for r in rows]

            if chunk_ids:
                placeholders = ",".join(["?"] * len(chunk_ids))
                conn.execute(
                    f"DELETE FROM fts_chunks WHERE chunk_id IN ({placeholders})",
                    chunk_ids,
                )
                conn.execute(
                    f"DELETE FROM chunk_meta WHERE chunk_id IN ({placeholders})",
                    chunk_ids,
                )
            logger.info("FTS5 deleted %d chunks for file_id=%s", len(chunk_ids), file_id)
            return len(chunk_ids)

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM chunk_meta").fetchone()
            return row["cnt"] if row else 0

    def is_indexed(self, file_id: str) -> bool:
        """Return ``True`` if *file_id* has any chunks in the index.

        Uses a direct ``chunk_meta`` lookup — no dummy embedding, no HNSW.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM chunk_meta WHERE file_id = ? LIMIT 1",
                (file_id,),
            ).fetchone()
            return row is not None

    def get_indexed_file_ids(self) -> set[str]:
        """Return the set of all ``file_id`` values currently indexed."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT file_id FROM chunk_meta"
            ).fetchall()
            return {r["file_id"] for r in rows}

    def get_by_chunk_id(self, chunk_id: str) -> DocumentChunk | None:
        """Retrieve a single chunk by ID."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    f.chunk_id, f.file_id, f.text, f.heading_path,
                    cm.chunk_level, cm.parent_chunk_id
                FROM fts_chunks f
                JOIN chunk_meta cm ON f.chunk_id = cm.chunk_id
                WHERE f.chunk_id = ?
                """,
                (chunk_id,),
            ).fetchone()

            if not row:
                return None

            return DocumentChunk(
                chunk_id=row["chunk_id"],
                file_id=row["file_id"],
                text=row["text"],
                heading_path=row["heading_path"].split(" > ") if row["heading_path"] else [],
                chunk_level=row["chunk_level"],
                parent_chunk_id=row["parent_chunk_id"] or None,
            )
