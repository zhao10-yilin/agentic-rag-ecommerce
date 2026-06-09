"""Custom exceptions for the PDF parser pipeline."""

from __future__ import annotations


class PDFParserError(Exception):
    """Base exception for all PDF parser errors."""


class ParseTimeoutError(PDFParserError):
    """Raised when a single PDF parse exceeds the configured timeout limit.

    Attributes:
        timeout_seconds: The maximum allowed processing time.
        file_id: Identifier of the file that timed out.
    """

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: int | None = None,
        file_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.file_id = file_id


class MemoryLimitExceeded(PDFParserError):
    """Raised when a worker process exceeds its memory budget.

    Attributes:
        memory_mb: Actual memory consumption at trigger time.
        limit_mb: Configured limit.
        file_id: Identifier of the file being processed.
    """

    def __init__(
        self,
        message: str,
        *,
        memory_mb: float,
        limit_mb: float,
        file_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.memory_mb = memory_mb
        self.limit_mb = limit_mb
        self.file_id = file_id
