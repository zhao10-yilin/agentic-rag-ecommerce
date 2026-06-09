"""FastAPI service for submitting and monitoring PDF parse jobs.

Environment variables
---------------------
PDF_PARSER_OUTPUT_DIR
    Directory where extracted images and side-car files are written
    (default: ``./output``).

Quick start
-----------
::

    # 1. Start Redis (broker + result backend)
    redis-server

    # 2. Start Celery worker(s)
    celery -A pdf_parser.tasks worker -c 4 --loglevel=info

    # 3. Start the API server
    uvicorn pdf_parser.api:app --host 0.0.0.0 --port 8000

    # 4. Submit a PDF
    curl -X POST "http://localhost:8000/parse" \
         -F "file=@/path/to/document.pdf"
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from celery.result import AsyncResult

from pdf_parser.tasks import app as celery_app, parse_pdf_task

logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Parser API",
    description="Asynchronous PDF parsing service powered by MinerU + Celery. "
                "RAG endpoints available under /rag.",
    version="2.0.0",
)

# Mount RAG sub-application (heavy models loaded lazily on first request)
from pdf_parser.rag.api import router as rag_router

app.include_router(rag_router, prefix="/rag")

_DEFAULT_OUTPUT_DIR = os.environ.get("PDF_PARSER_OUTPUT_DIR", "./output")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok", "service": "pdf_parser_api"}


# ---------------------------------------------------------------------------
# Parse submission
# ---------------------------------------------------------------------------


@app.post("/parse")
async def submit_parse(
    file: UploadFile | None = File(
        None,
        description="PDF file to upload.  Mutually exclusive with *file_path*.",
    ),
    file_path: str | None = Query(
        None,
        description="Server-side absolute path to an existing PDF file. "
                    "The Celery worker must be able to read this path.",
    ),
    enable_ocr: bool = Query(True, description="Enable OCR for scanned pages."),
    extract_images: bool = Query(
        True, description="Extract embedded images and rewrite Markdown links."
    ),
    timeout_seconds: int = Query(
        300, ge=1, description="Per-file parsing timeout in seconds."
    ),
    enable_cleaning: bool = Query(
        True, description="Enable the 4-layer text cleaning pipeline."
    ),
    output_dir: str | None = Query(
        None,
        description="Override the default output directory for this request."
                    " Extracted images and side-car files are written here.",
    ),
) -> dict[str, Any]:
    """Submit a PDF for asynchronous parsing.

    Provide **either** an uploaded file **or** a server-side ``file_path``.
    The API immediately returns a ``task_id`` that can be polled via
    ``GET /status/{task_id}`` or ``GET /result/{task_id}``.

    Resource-pool control is handled by the Celery worker concurrency
    (``-c`` flag).  If all workers are busy, tasks queue transparently
    in Redis until a slot becomes available.
    """
    target_path: str
    resolved_file_id: str

    if file is not None:
        # Stream upload to a temporary file on the API host.
        # NOTE: In a distributed deployment the worker must share this
        # filesystem (e.g. NFS, EFS, or S3 FUSE mount).
        _dest_dir = output_dir or _DEFAULT_OUTPUT_DIR
        Path(_dest_dir).mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "upload.pdf").suffix
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=_dest_dir
        ) as tmp:
            content = await file.read()
            tmp.write(content)
            target_path = tmp.name
        resolved_file_id = Path(file.filename or "upload").stem
        logger.info(
            "Received upload",
            extra={"file_id": resolved_file_id, "temp_path": target_path},
        )

    elif file_path:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {file_path}",
            )
        target_path = str(path)
        resolved_file_id = path.stem

    else:
        raise HTTPException(
            status_code=400,
            detail="Either 'file' (multipart upload) or 'file_path' (query param) is required.",
        )

    parser_config: dict[str, Any] = {
        "enable_ocr": enable_ocr,
        "extract_images": extract_images,
        "timeout_seconds": timeout_seconds,
        "output_dir": output_dir or _DEFAULT_OUTPUT_DIR,
    }

    cleaning_config: dict[str, Any] | None = None
    if enable_cleaning:
        cleaning_config = {
            "repetition_filter": True,
            "ocr_corrector": True,
            "paragraph_repair": True,
        }

    task = parse_pdf_task.delay(
        target_path,
        file_id=resolved_file_id,
        parser_config=parser_config,
        cleaning_config=cleaning_config,
    )

    return {
        "task_id": task.id,
        "status": "submitted",
        "file_id": resolved_file_id,
        "monitor_url": f"/status/{task.id}",
    }


# ---------------------------------------------------------------------------
# Status & result polling
# ---------------------------------------------------------------------------


@app.get("/status/{task_id}")
async def get_status(task_id: str) -> dict[str, Any]:
    """Query the status of a submitted parse task.

    Possible statuses: **PENDING**, **STARTED**, **SUCCESS**, **FAILURE**,
    **RETRY**.
    """
    result = AsyncResult(task_id, app=celery_app)
    response: dict[str, Any] = {
        "task_id": task_id,
        "status": result.status,
    }
    if result.ready():
        if result.successful():
            response["result"] = result.result
        else:
            response["error"] = str(result.result)
    return response


@app.get("/result/{task_id}")
async def get_result(task_id: str) -> dict[str, Any]:
    """Fetch the final result of a parse task.

    Returns **202 Accepted** if the task is still running.
    """
    result = AsyncResult(task_id, app=celery_app)
    if not result.ready():
        return JSONResponse(
            status_code=202,
            content={"task_id": task_id, "status": result.status},
        )
    if result.successful():
        return {
            "task_id": task_id,
            "status": "success",
            "result": result.result,
        }
    raise HTTPException(status_code=500, detail=str(result.result))
