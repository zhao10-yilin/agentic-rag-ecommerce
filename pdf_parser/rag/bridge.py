"""Bridge between the PDF checkpoint system and the RAG indexer.

Problem
-------
``TransactionalCheckpoint`` tracks "PDF parsed successfully".
``RAGIndexer`` tracks "chunks exist in vector store".
They don't know about each other.

Solution
--------
:class:`RAGCheckpointBridge` connects them.  It can tell you:

* Which successfully-parsed files have **not yet been indexed** (incremental
  indexing candidates).
* Which indexed files have **no matching checkpoint entry** (orphaned indexes,
  e.g. from manual ``/rag/index`` calls that bypassed the orchestrator).
* Mark files as indexed when the indexer finishes, so the next scan skips them.

Usage::

    from pdf_parser.checkpoint_sqlite import TransactionalCheckpoint
    from pdf_parser.rag.vector_store import SQLiteFTSStore

    checkpoint = TransactionalCheckpoint("pipeline.db")
    fts = SQLiteFTSStore()

    bridge = RAGCheckpointBridge(checkpoint=checkpoint, fts_store=fts)

    # Find files that need indexing
    to_index = bridge.get_files_to_index()
    print(f"{len(to_index)} files parsed but not yet indexed")

    # After indexing, mark them
    for file_id in to_index:
        indexer.index_parse_result(result)
        bridge.mark_indexed(file_id)

    # Audit consistency
    report = bridge.report()
    print(report)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal protocol for the checkpoint — avoids a hard import dependency
# ---------------------------------------------------------------------------


class _CheckpointLike(Protocol):
    """Subset of :class:`TransactionalCheckpoint` that the bridge needs."""

    checkpoint_file: Path

    def is_processed(self, file_id: str) -> bool: ...
    def get_failure_count(self, file_id: str) -> int: ...

    @property
    def processed_count(self) -> int: ...


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class RAGCheckpointBridge:
    """Connect a PDF processing checkpoint with the RAG index state.

    Parameters
    ----------
    checkpoint:
        A :class:`TransactionalCheckpoint` (or any object satisfying the
        protocol — see :class:`_CheckpointLike`).
    fts_store:
        The :class:`SQLiteFTSStore` where chunks are indexed.  This is used
        for both querying index state and persisting the bridge's own
        ``indexed_at`` tracking.

    Notes
    -----
    The bridge adds a lightweight ``_index_log`` table to *fts_store*'s
    SQLite database.  This avoids yet another file and keeps the index
    status next to the chunk metadata.
    """

    def __init__(
        self,
        checkpoint: _CheckpointLike,
        fts_store: Any,  # SQLiteFTSStore
    ) -> None:
        self._checkpoint = checkpoint
        self._fts = fts_store
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._fts._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS _index_log (
                    file_id TEXT PRIMARY KEY,
                    indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    notes TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_index_log_at ON _index_log(indexed_at)"
            )

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_indexed(self, file_id: str) -> bool:
        """Check whether *file_id* has been recorded as indexed."""
        with self._fts._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM _index_log WHERE file_id = ? LIMIT 1",
                (file_id,),
            ).fetchone()
            return row is not None

    def get_files_to_index(
        self,
        *,
        check_fts: bool = True,
    ) -> list[str]:
        """Return file_ids that are parsed but not yet indexed.

        These are candidates for incremental indexing.  The list is built
        by querying the checkpoint for files with ``status='success'`` that
        do **not** have an entry in ``_index_log``.

        When *check_fts* is ``True`` (default), the method also checks the
        ``chunk_meta`` table — a file with chunks present but no index_log
        entry is considered already-indexed (log entry will be backfilled).
        """
        candidates: list[str] = []
        cp_db = self._checkpoint.checkpoint_file

        with sqlite3.connect(str(cp_db), timeout=30.0) as cp_conn:
            rows = cp_conn.execute(
                "SELECT file_id FROM checkpoint WHERE status = 'success'"
            ).fetchall()
            checkpoint_ids = {r[0] for r in rows}

        if not checkpoint_ids:
            return []

        with self._fts._connect() as fts_conn:
            # Already logged as indexed
            logged_rows = fts_conn.execute(
                "SELECT file_id FROM _index_log"
            ).fetchall()
            logged_ids = {r["file_id"] for r in logged_rows}

            for fid in sorted(checkpoint_ids - logged_ids):
                if check_fts:
                    # If chunks exist in chunk_meta, backfill the log
                    row = fts_conn.execute(
                        "SELECT 1 FROM chunk_meta WHERE file_id = ? LIMIT 1",
                        (fid,),
                    ).fetchone()
                    if row:
                        # Chunks exist — mark as indexed retroactively
                        fts_conn.execute(
                            "INSERT OR IGNORE INTO _index_log (file_id, notes) VALUES (?, 'backfilled')",
                            (fid,),
                        )
                        logger.info("Backfilled index log for %s", fid)
                        continue
                candidates.append(fid)

        return candidates

    def get_orphaned_indexes(self) -> list[str]:
        """Return indexed file_ids that have NO matching checkpoint entry.

        These usually come from ``/rag/index`` calls that bypassed the
        orchestrator, or from the checkpoint database being on a different
        filesystem.
        """
        cp_db = self._checkpoint.checkpoint_file

        with sqlite3.connect(str(cp_db), timeout=30.0) as cp_conn:
            checkpoint_ids = {
                r[0] for r in cp_conn.execute(
                    "SELECT file_id FROM checkpoint"
                ).fetchall()
            }

        with self._fts._connect() as fts_conn:
            indexed_rows = fts_conn.execute(
                "SELECT file_id FROM _index_log"
            ).fetchall()
            indexed_ids = {r["file_id"] for r in indexed_rows}

        return sorted(indexed_ids - checkpoint_ids)

    def get_stale_indexes(self) -> list[str]:
        """Return file_ids where the checkpoint says 'processing' or 'failed'
        but chunks are indexed — possibly inconsistent state.
        """
        cp_db = self._checkpoint.checkpoint_file

        with sqlite3.connect(str(cp_db), timeout=30.0) as cp_conn:
            non_success = {
                r[0] for r in cp_conn.execute(
                    "SELECT file_id FROM checkpoint WHERE status != 'success'"
                ).fetchall()
            }

        indexed = self._fts.get_indexed_file_ids()
        return sorted(indexed & non_success)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def mark_indexed(
        self,
        file_id: str,
        *,
        chunk_count: int = 0,
        notes: str | None = None,
    ) -> None:
        """Record that *file_id* has been successfully indexed."""
        with self._fts._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO _index_log (file_id, chunk_count, notes)
                VALUES (?, ?, ?)
                """,
                (file_id, chunk_count, notes or ""),
            )
        logger.debug("Marked %s as indexed", file_id)

    def mark_unindexed(self, file_id: str) -> None:
        """Remove the index log entry for *file_id*.

        Call this after deleting all chunks for a file.
        """
        with self._fts._connect() as conn:
            conn.execute(
                "DELETE FROM _index_log WHERE file_id = ?",
                (file_id,),
            )
        logger.debug("Marked %s as unindexed", file_id)

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Produce a consistency report.

        Returns a dict with counts for each category, suitable for logging
        or displaying in a monitoring dashboard.
        """
        parsed = self._checkpoint.processed_count
        indexed = self.indexed_count
        to_index = self.get_files_to_index()
        orphaned = self.get_orphaned_indexes()
        stale = self.get_stale_indexes()

        return {
            "parsed_success": parsed,
            "indexed": indexed,
            "files_to_index": len(to_index),
            "files_to_index_list": to_index[:20],  # first 20 for display
            "orphaned_indexes": len(orphaned),
            "orphaned_list": orphaned[:20],
            "stale_indexes": len(stale),
            "stale_list": stale[:20],
            "consistent": len(to_index) == 0 and len(orphaned) == 0 and len(stale) == 0,
        }

    @property
    def indexed_count(self) -> int:
        """Number of files recorded as indexed."""
        with self._fts._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM _index_log").fetchone()
            return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def sync(self) -> dict[str, int]:
        """One-shot repair pass.

        * Backfills index_log for files with chunks but no log entry.
        * Removes index_log entries for file_ids with no chunks in chunk_meta
          (cleanup after manual deletion that bypassed the bridge).

        Returns ``{"backfilled": N, "cleaned": M}``.
        """
        backfilled = 0
        cleaned = 0

        with self._fts._connect() as conn:
            # Backfill: chunk_meta has it, index_log doesn't
            rows = conn.execute(
                """
                SELECT DISTINCT cm.file_id
                FROM chunk_meta cm
                WHERE NOT EXISTS (
                    SELECT 1 FROM _index_log il WHERE il.file_id = cm.file_id
                )
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO _index_log (file_id, notes) VALUES (?, 'sync_backfill')",
                    (row["file_id"],),
                )
                backfilled += 1

            # Cleanup: index_log has it, chunk_meta doesn't
            rows = conn.execute(
                """
                SELECT il.file_id
                FROM _index_log il
                WHERE NOT EXISTS (
                    SELECT 1 FROM chunk_meta cm WHERE cm.file_id = il.file_id
                )
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    "DELETE FROM _index_log WHERE file_id = ?",
                    (row["file_id"],),
                )
                cleaned += 1

        if backfilled or cleaned:
            logger.info(
                "Bridge sync: backfilled=%d cleaned=%d", backfilled, cleaned
            )
        return {"backfilled": backfilled, "cleaned": cleaned}
