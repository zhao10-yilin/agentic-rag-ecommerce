"""Checkpoint manager for resumable batch processing."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Tracks successfully processed ``file_id`` values in an append-only file.

    The file format is one ``file_id`` per line.  Append-only writes guarantee
    that even if the process crashes mid-flight, already-recorded successes are
    never lost.

    Advisory locking (``fcntl``) is used on Unix for multi-process safety.
    On Windows the lock is a no-op because the orchestrator serialises writes
    through a single main process.
    """

    def __init__(self, checkpoint_file: str | Path) -> None:
        self._file = Path(checkpoint_file)
        self._processed: set[str] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_processed(self, file_id: str) -> bool:
        """Return ``True`` if *file_id* has already been recorded."""
        return file_id in self._processed

    def add(self, file_id: str) -> None:
        """Append *file_id* to the checkpoint file and in-memory set."""
        if file_id in self._processed:
            return

        self._processed.add(file_id)
        try:
            with self._file.open("a", encoding="utf-8") as fh:
                self._lock(fh)
                fh.write(f"{file_id}\n")
                fh.flush()
                os.fsync(fh.fileno())
                self._unlock(fh)
        except OSError as exc:
            logger.warning(
                "Failed to persist checkpoint for %s: %s", file_id, exc
            )

    def filter_pending(self, file_paths: Iterable[Path]) -> list[Path]:
        """Return only files that have **not** been processed yet."""
        pending: list[Path] = []
        for path in file_paths:
            file_id = path.stem
            if self.is_processed(file_id):
                logger.info("Skipping already processed file: %s", file_id)
            else:
                pending.append(path)
        return pending

    @property
    def checkpoint_file(self) -> Path:
        """Path to the underlying checkpoint file."""
        return self._file

    @property
    def processed_count(self) -> int:
        """Number of unique file_ids recorded so far."""
        return len(self._processed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> set[str]:
        """Read the checkpoint file from disk."""
        if not self._file.exists():
            return set()
        try:
            with self._file.open("r", encoding="utf-8") as fh:
                return {line.strip() for line in fh if line.strip()}
        except OSError as exc:
            logger.warning("Failed to read checkpoint file %s: %s", self._file, exc)
            return set()

    @staticmethod
    def _lock(fh) -> None:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock(fh) -> None:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
