"""Celery tasks for asynchronous PDF parsing.

Usage (local development)::

    # Terminal 1 — start the API
    uvicorn pdf_parser.api:app --reload --port 8000

    # Terminal 2 — start a Celery worker
    celery -A pdf_parser.tasks worker -c 4 --loglevel=info

    # Terminal 3 — submit a job via curl
    curl -X POST "http://localhost:8000/parse" \
         -F "file=@/path/to/document.pdf"

Production deployment notes
---------------------------
* The worker and API **must share a filesystem** (or use S3/NFS) when
  ``file_path`` mode is used, because the worker needs to read the file
  from the path returned by the API.
* On Windows Celery does not support the ``prefork`` pool fully; use
  ``-P solo`` or run the worker inside WSL2.
* GPU memory is released only when the worker process exits.
  ``worker_max_tasks_per_child = 1`` guarantees this after every task.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import Celery

from pdf_parser.cleaning import TextCleaner
from pdf_parser.models import ParseResult, Status
from pdf_parser.strategies import MinerUParser

logger = logging.getLogger(__name__)

app = Celery("pdf_parser")
app.config_from_object("pdf_parser.celeryconfig")


@app.task(bind=True, max_retries=3)
def parse_pdf_task(
    self,
    file_path: str,
    *,
    file_id: str | None = None,
    parser_config: dict[str, Any] | None = None,
    cleaning_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse a single PDF file asynchronously.

    This task is the bridge between the HTTP API and the core
    :class:`MinerUParser`.  It runs inside a Celery worker process, so
    all CPU / GPU heavy work happens off the API thread.

    Args:
        file_path: Absolute path to the PDF file **on the worker filesystem**.
        file_id: Optional identifier; falls back to the file stem.
        parser_config: Forwarded to :class:`MinerUParser`.
        cleaning_config: Forwarded to :class:`TextCleaner`.

    Returns:
        JSON-serializable dict representation of :class:`ParseResult`.

    Raises:
        celery.exceptions.Retry: On transient failures (up to 3 retries).
    """
    path = Path(file_path)
    resolved_id = file_id or path.stem

    logger.info(
        "Starting parse task",
        extra={"file_id": resolved_id, "file_path": str(path)},
    )

    try:
        parser = MinerUParser(config=(parser_config or {}))
        result: ParseResult = parser.parse(path, file_id=resolved_id)

        # Apply the same 4-layer cleaning pipeline used by the CLI
        if result.status == Status.SUCCESS and cleaning_config:
            cleaner = TextCleaner(config=cleaning_config)
            cleaned = cleaner.clean(result.markdown_content)
            if cleaned != result.markdown_content:
                result = result.model_copy(update={"cleaned_markdown": cleaned})

        logger.info(
            "Parse task complete",
            extra={
                "file_id": resolved_id,
                "status": result.status.value,
                "pages": result.metrics.page_count if result.metrics else 0,
                "images": result.metrics.image_count if result.metrics else 0,
            },
        )
        return result.model_dump(mode="json")

    except Exception as exc:
        logger.exception(
            "Parse task failed",
            extra={"file_id": resolved_id},
        )
        # Retry with exponential backoff — useful when the failure is due to
        # transient resource exhaustion (e.g. GPU OOM, disk full).
        raise self.retry(exc=exc, countdown=60)
