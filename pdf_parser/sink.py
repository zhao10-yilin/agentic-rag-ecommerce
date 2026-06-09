"""DataSink — persist parsing results to JSONL and dead-letter log files.

The sink follows an **append-only** philosophy so that crash-recovery is
automatic: every ``write_success`` / ``write_failure`` call flushes and
``fsync`` s to disk before returning, ensuring that committed records survive
a sudden power loss or ``kill -9``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from pdf_parser.models import ParseResult, Status

logger = logging.getLogger(__name__)


class DataSink:
    """Serializes :class:`ParseResult` objects to JSONL and a dead-letter log.

    Usage (context manager)::

        with DataSink(output_dir="./output") as sink:
            for result in results:
                sink.emit(result)

    Usage (explicit open/close)::

        sink = DataSink(output_dir="./output")
        sink.open()
        for result in results:
            sink.emit(result)
        sink.close()
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        jsonl_filename: str = "parsed_results.jsonl",
        failed_log_filename: str = "failed_jobs.log",
        encoding: str = "utf-8",
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.jsonl_path = self.output_dir / jsonl_filename
        self.failed_log_path = self.output_dir / failed_log_filename
        self.encoding = encoding

        self._jsonl_fh: Any = None
        self._failed_fh: Any = None
        self._success_count = 0
        self._failure_count = 0

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _truncate_broken_tail(self) -> None:
        """Remove an incomplete trailing line from the JSONL file.

        When the OS or process crashes between ``write()`` and ``fsync()``,
        the JSONL file can end with a partial JSON object.  This method scans
        the tail of the file for the last newline and truncates everything
        after it, guaranteeing that every line is parseable.
        """
        if not self.jsonl_path.exists() or self.jsonl_path.stat().st_size == 0:
            return

        with self.jsonl_path.open("r+", encoding=self.encoding, newline="") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return

            # Read the last chunk (up to 8 KiB) backwards from the end
            chunk_size = min(size, 8192)
            fh.seek(-chunk_size, os.SEEK_END)
            chunk = fh.read(chunk_size)

            last_newline = chunk.rfind("\n")
            if last_newline == -1:
                logger.warning(
                    "JSONL tail has no newline in last %d bytes; possible corruption: %s",
                    chunk_size,
                    self.jsonl_path,
                )
                return

            # valid_size = bytes before the chunk + position of newline + 1
            valid_size = size - (len(chunk) - last_newline - 1)
            if valid_size < size:
                fh.seek(valid_size)
                fh.truncate()
                logger.info(
                    "Truncated broken JSONL tail (%d → %d bytes)",
                    size,
                    valid_size,
                )

    def open(self) -> Self:
        """Open underlying file handles in append mode.

        Before appending, the JSONL file is inspected for a broken trailing
        line (e.g. from a crash mid-write) and truncated to the last complete
        newline so that downstream consumers always see valid JSON lines.
        """
        if self._jsonl_fh is not None:
            return self

        self._truncate_broken_tail()

        self._jsonl_fh = self.jsonl_path.open(
            "a", encoding=self.encoding, newline=""
        )
        self._failed_fh = self.failed_log_path.open(
            "a", encoding=self.encoding, newline=""
        )

        logger.info(
            "DataSink opened",
            extra={
                "jsonl": str(self.jsonl_path),
                "failed_log": str(self.failed_log_path),
            },
        )
        return self

    def close(self) -> None:
        """Flush buffers, sync to disk, and close file handles."""
        if self._jsonl_fh is not None:
            self._jsonl_fh.flush()
            os.fsync(self._jsonl_fh.fileno())
            self._jsonl_fh.close()
            self._jsonl_fh = None

        if self._failed_fh is not None:
            self._failed_fh.flush()
            os.fsync(self._failed_fh.fileno())
            self._failed_fh.close()
            self._failed_fh = None

        logger.info(
            "DataSink closed — %d success, %d failure records written",
            self._success_count,
            self._failure_count,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, result: ParseResult) -> None:
        """Route a *result* to the appropriate stream based on its status."""
        if result.status == Status.SUCCESS:
            self.write_success(result)
        else:
            self.write_failure(result)

    def write_success(self, result: ParseResult) -> None:
        """Append a successful :class:`ParseResult` to the JSONL file.

        The record is serialized with ``model_dump(mode='json')`` so that
        Pydantic types (``datetime``, ``Enum``, etc.) are converted to plain
        JSON primitives automatically.
        """
        if self._jsonl_fh is None:
            raise RuntimeError("DataSink is not open. Call .open() or use 'with'.")

        record = result.model_dump(mode="json")
        line = json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":"))

        self._jsonl_fh.write(line + "\n")
        self._jsonl_fh.flush()
        os.fsync(self._jsonl_fh.fileno())

        self._success_count += 1
        logger.debug("Wrote success record for %s", result.file_id)

    def write_failure(self, result: ParseResult) -> None:
        """Append a failed job to the dead-letter log (failed_jobs.log).

        The dead-letter record is a minimal JSON object containing only the
        fields needed for later triage:

            * ``file_id``
            * ``status``
            * ``error_msg``
            * ``timestamp`` — ISO-8601 UTC when the sink received the record
        """
        if self._failed_fh is None:
            raise RuntimeError("DataSink is not open. Call .open() or use 'with'.")

        dlq_record = {
            "file_id": result.file_id,
            "status": result.status.value,
            "error_msg": result.error_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        line = json.dumps(dlq_record, ensure_ascii=False, separators=(",", ":"))

        self._failed_fh.write(line + "\n")
        self._failed_fh.flush()
        os.fsync(self._failed_fh.fileno())

        self._failure_count += 1
        logger.warning(
            "Wrote dead-letter record for %s: %s",
            result.file_id,
            result.error_msg,
        )

    @property
    def success_count(self) -> int:
        return self._success_count

    @property
    def failure_count(self) -> int:
        return self._failure_count
