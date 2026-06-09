"""Structured JSON logging configuration for production observability."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects.

    Designed for log aggregation stacks (ELK, Loki, Fluent Bit, etc.).
    """

    def __init__(
        self,
        *,
        fmt_keys: dict[str, str] | None = None,
    ) -> None:
        """Initialize the formatter.

        Args:
            fmt_keys: Mapping from JSON output key to LogRecord attribute name.
                Defaults to a sensible production-ready set.
        """
        super().__init__()
        self.fmt_keys = fmt_keys or {
            "timestamp": "timestamp",
            "level": "levelname",
            "logger": "name",
            "message": "message",
            "module": "module",
            "function": "funcName",
            "line": "lineno",
            "thread": "thread",
            "thread_name": "threadName",
        }

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string."""
        message = self._prepare_log_dict(record)
        return json.dumps(message, ensure_ascii=False, default=str)

    def _prepare_log_dict(self, record: logging.LogRecord) -> dict[str, Any]:
        """Build a dictionary that mirrors the final JSON shape."""
        output: dict[str, Any] = {}

        for json_key, record_attr in self.fmt_keys.items():
            value = getattr(record, record_attr, None)
            if value is not None:
                output[json_key] = value

        # Always use ISO-8601 UTC timestamp
        output["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Merge structured extra fields added via ``logging.info("msg", extra={...})``
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            output["extra"] = record.extra

        # Capture exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            output["exception"] = self.formatException(record.exc_info)

        # Merge any remaining attributes that are not standard LogRecord fields
        # but were injected via ``extra=``
        standard_attrs = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())
        standard_attrs |= {"message", "asctime", "timestamp", "extra", "exception"}
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                output.setdefault("extra", {})[key] = value

        return output


def configure_logging(
    level: int = logging.INFO,
    *,
    handlers: list[logging.Handler] | None = None,
) -> None:
    """Configure root logger for structured JSON output.

    Args:
        level: Minimum log level (default ``INFO``).
        handlers: Optional list of handlers. If omitted, a single
            :class:`logging.StreamHandler` writing to ``stdout`` is used.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Prevent duplicate handlers if called multiple times
    for hdl in list(root.handlers):
        root.removeHandler(hdl)

    if handlers is None:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(JSONFormatter())
        handlers = [stream_handler]

    for hdl in handlers:
        root.addHandler(hdl)

    # Silence overly chatty third-party libraries in production
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pdfplumber").setLevel(logging.WARNING)
