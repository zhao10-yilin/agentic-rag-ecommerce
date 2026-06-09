"""Structured JSON logger factory.

Produces loggers with consistent formatting and contextual fields.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Emit log records as JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
        if hasattr(record, "trace_id"):
            payload["trace_id"] = record.trace_id  # type: ignore[attr-defined]
        if hasattr(record, "span_id"):
            payload["span_id"] = record.span_id  # type: ignore[attr-defined]

        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    fmt: str = "json",
) -> None:
    """Configure the root logger for the agentic_rag package.

    Parameters
    ----------
    level:
        Log level string (DEBUG, INFO, WARNING, ERROR).
    fmt:
        ``"json"`` or ``"text"``.
    """
    root = logging.getLogger("agentic_rag")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if root.handlers:
        return  # Already configured

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger child of ``agentic_rag``."""
    return logging.getLogger(f"agentic_rag.{name}")
