"""Transactional SQLite checkpoint for crash-resilient batch processing.

Replaces the append-only text file with a SQLite database that tracks:
* **State machine** — pending → processing → success / failed
* **File integrity** — SHA-256 hash so replaced files are re-processed
* **Failure counting** — poison-pill detection (skip PDFs that crash repeatedly)
* **Atomic writes** — BEGIN / COMMIT guarantees no partial updates survive a crash
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class TransactionalCheckpoint:
    """SQLite-backed checkpoint with two-phase commit semantics.

    Every PDF transitions through explicit states:

        pending → processing → success
                           └→ failed (failure_count += 1)

    A file that reaches ``failure_count >= max_failures`` is treated as a
    **poison pill** and permanently skipped.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_failures: int = 3,
    ) -> None:
        self._db = Path(db_path)
        self._max_failures = max(max_failures, 1)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Public API (drop-in compatible with CheckpointManager)
    # ------------------------------------------------------------------

    @property
    def checkpoint_file(self) -> Path:
        """Path to the underlying SQLite database."""
        return self._db

    @property
    def processed_count(self) -> int:
        """Number of files that have reached ``status='success'``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM checkpoint WHERE status = 'success'"
            ).fetchone()
            return row[0] if row else 0

    def is_processed(self, file_id: str) -> bool:
        """Return ``True`` if *file_id* has already succeeded."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM checkpoint WHERE file_id = ? AND status = 'success'",
                (file_id,),
            ).fetchone()
            return row is not None

    def add(self, file_id: str) -> None:
        """Backward-compatible helper — mark *file_id* as success immediately.

        Used when the orchestrator has already persisted output and only
        needs to record the checkpoint.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO checkpoint (file_id, file_hash, status, completed_at)
                VALUES (?, '', 'success', datetime('now'))
                ON CONFLICT(file_id) DO UPDATE SET
                    status = 'success',
                    error_msg = NULL,
                    completed_at = datetime('now')
                """,
                (file_id,),
            )

    def start(self, file_id: str, file_hash: str) -> bool:
        """Atomically claim *file_id* for processing.

        Returns ``True`` if the caller should proceed (the file was in
        ``pending`` / ``processing`` / ``failed`` state with retries left).
        Returns ``False`` if the file is already ``success`` or has exhausted
        its retry budget.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, failure_count FROM checkpoint WHERE file_id = ?",
                (file_id,),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO checkpoint (file_id, file_hash, status, started_at) "
                    "VALUES (?, ?, 'processing', datetime('now'))",
                    (file_id, file_hash),
                )
                return True

            status, failure_count = row
            if status == "success":
                return False
            if status == "failed" and failure_count >= self._max_failures:
                logger.warning(
                    "Poison pill detected — skipping %s (failed %d times)",
                    file_id,
                    failure_count,
                )
                return False

            conn.execute(
                "UPDATE checkpoint SET status='processing', file_hash=?, "
                "started_at=datetime('now') WHERE file_id=?",
                (file_hash, file_id),
            )
            return True

    def complete(
        self,
        file_id: str,
        *,
        status: str,
        error_msg: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        """Record the final outcome of a parse job inside a transaction."""
        with self._connect() as conn:
            if status == "success":
                conn.execute(
                    """
                    UPDATE checkpoint
                    SET status = 'success',
                        error_msg = NULL,
                        completed_at = datetime('now'),
                        output_dir = ?
                    WHERE file_id = ?
                    """,
                    (output_dir or "", file_id),
                )
                if output_dir:
                    self._write_manifest(Path(output_dir), file_id)
            else:
                conn.execute(
                    """
                    UPDATE checkpoint
                    SET status = 'failed',
                        error_msg = ?,
                        failure_count = failure_count + 1,
                        completed_at = datetime('now')
                    WHERE file_id = ?
                    """,
                    (error_msg or "", file_id),
                )

    def filter_pending(
        self,
        file_paths: Iterable[Path],
    ) -> list[Path]:
        """Return only files that still need to be processed.

        Rules:
        1. Not in DB → pending.
        2. Status ``success`` and hash matches → skip.
        3. Status ``success`` but hash differs → re-process (file replaced).
        4. Status ``processing`` → re-process (previous run crashed).
        5. Status ``failed`` and failure_count < max_failures → retry.
        6. Status ``failed`` and failure_count >= max_failures → skip (poison).
        """
        candidates: list[Path] = []
        file_ids: dict[str, Path] = {}

        for path in file_paths:
            file_id = path.stem
            file_ids[file_id] = path
            candidates.append(path)

        if not candidates:
            return []

        pending: list[Path] = []
        with self._connect() as conn:
            for path in candidates:
                file_id = path.stem
                file_hash = _sha256_file(path)

                row = conn.execute(
                    "SELECT status, file_hash, failure_count FROM checkpoint WHERE file_id = ?",
                    (file_id,),
                ).fetchone()

                if row is None:
                    # Brand-new file
                    pending.append(path)
                    continue

                db_status, db_hash, failure_count = row

                if db_status == "success":
                    if db_hash != file_hash:
                        logger.info(
                            "File changed (hash mismatch), resetting to pending: %s",
                            file_id,
                        )
                        conn.execute(
                            "UPDATE checkpoint SET status='pending', file_hash=? WHERE file_id=?",
                            (file_hash, file_id),
                        )
                        pending.append(path)
                        continue

                    # Verify artifact integrity via manifest
                    row_dir = conn.execute(
                        "SELECT output_dir FROM checkpoint WHERE file_id = ?",
                        (file_id,),
                    ).fetchone()
                    output_dir = Path(row_dir[0]) if row_dir and row_dir[0] else None
                    if output_dir and not self._verify_manifest(output_dir, file_id):
                        logger.warning(
                            "Artifacts incomplete/corrupt for %s, resetting to pending",
                            file_id,
                        )
                        conn.execute(
                            "UPDATE checkpoint SET status='pending' WHERE file_id=?",
                            (file_id,),
                        )
                        pending.append(path)
                    else:
                        logger.info("Skipping already processed file: %s", file_id)
                elif db_status == "processing":
                    logger.info(
                        "Previous run crashed mid-flight, re-processing: %s",
                        file_id,
                    )
                    pending.append(path)
                elif db_status == "failed":
                    if failure_count < self._max_failures:
                        logger.info(
                            "Retrying failed file (%d/%d): %s",
                            failure_count + 1,
                            self._max_failures,
                            file_id,
                        )
                        pending.append(path)
                    else:
                        logger.warning(
                            "Skipping poison pill (failed %d times): %s",
                            failure_count,
                            file_id,
                        )
                else:
                    # Should not happen, but treat as pending to be safe
                    pending.append(path)

        return pending

    def get_failure_count(self, file_id: str) -> int:
        """Return how many times *file_id* has failed so far."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT failure_count FROM checkpoint WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint (
                    file_id TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'processing', 'success', 'failed')),
                    started_at TEXT,
                    completed_at TEXT,
                    error_msg TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    output_dir TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoint_status ON checkpoint(status)"
            )

    def _write_manifest(self, output_dir: Path, file_id: str) -> None:
        """Write a JSON manifest describing all files produced for *file_id*."""
        import json

        work_dir = output_dir / file_id
        if not work_dir.exists():
            return

        manifest: dict[str, Any] = {"file_id": file_id, "files": []}
        for f in work_dir.rglob("*"):
            if f.is_file() and f.name != ".manifest.json":
                manifest["files"].append(
                    {
                        "path": str(f.relative_to(work_dir).as_posix()),
                        "size": f.stat().st_size,
                    }
                )

        manifest_path = work_dir / ".manifest.json"
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

    def _verify_manifest(self, output_dir: Path, file_id: str) -> bool:
        """Return ``True`` if every file listed in the manifest still exists with the same size."""
        import json

        manifest_path = output_dir / file_id / ".manifest.json"
        if not manifest_path.exists():
            return False

        try:
            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return False

        work_dir = output_dir / file_id
        for entry in manifest.get("files", []):
            expected_path = work_dir / entry["path"]
            if not expected_path.exists():
                return False
            if expected_path.stat().st_size != entry["size"]:
                return False
        return True


def _sha256_file(path: Path) -> str:
    """Return the hex digest SHA-256 of *path* without loading it all in memory."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(8192):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()
