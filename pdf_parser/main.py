#!/usr/bin/env python3
"""Production-grade PDF non-standard data parsing pipeline — full entry script.

Usage::

    # Minimal run (reads ./input_pdfs, writes to ./output)
    python -m pdf_parser.main

    # Full control via CLI flags
    python -m pdf_parser.main \
        --input-dir  /data/raw_pdfs    \
        --output-dir /data/extracted   \
        --workers 4                    \
        --timeout 300                  \
        --log-level INFO

    # Environment variables (fallback when flags are omitted)
    export PDF_INPUT_DIR=./input_pdfs
    export PDF_OUTPUT_DIR=./output
    export PDF_PARSER_WORKERS=4
    export PDF_PARSER_MAX_MEMORY_MB=4096
    python -m pdf_parser.main

Resumability
------------
The pipeline maintains two durability layers:

1. **Checkpoint** (``.processed_files``) — orchestrator-level; ensures that
   a file which has already been successfully parsed is never re-submitted.
2. **JSONL sink** (``parsed_results.jsonl``) — result-level; every success
   is flushed to disk immediately so that a crash never loses committed work.

Dead-letter queue
-----------------
Failed tasks are written to ``failed_jobs.log`` as single-line JSON objects,
ready for triage or replay without touching the success stream.

Hook: deduplication
-------------------
After the batch completes, ``run_deduplication(jsonl_path)`` is called.
Replace the stub in :mod:`pdf_parser.hooks` with your LSH module integration.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from pdf_parser.hooks import run_deduplication
from pdf_parser.logging_config import configure_logging
from pdf_parser.models import Status
from pdf_parser.orchestrator import PipelineOrchestrator
from pdf_parser.sink import DataSink
from pdf_parser.strategies import MinerUParser

logger = logging.getLogger("pdf_parser.main")


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with sensible defaults."""
    parser = argparse.ArgumentParser(
        prog="pdf_parser",
        description="Batch PDF parser with process parallelism, retry, and checkpointing.",
    )

    # -- Directories -----------------------------------------------------
    parser.add_argument(
        "--input-dir",
        default=os.environ.get("PDF_INPUT_DIR", "./input_pdfs"),
        help="Directory containing PDF files to process "
             "(default: $PDF_INPUT_DIR or ./input_pdfs)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("PDF_OUTPUT_DIR", "./output"),
        help="Directory where JSONL, images, and logs are written "
             "(default: $PDF_OUTPUT_DIR or ./output)",
    )

    # -- Concurrency -----------------------------------------------------
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker processes. "
             "None = auto-detect from $PDF_PARSER_WORKERS or CPU count.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-file parsing timeout in seconds (default: 300 = 5 min).",
    )

    # -- Parser features -------------------------------------------------
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--enable-ocr",
        dest="enable_ocr",
        action="store_true",
        default=True,
        help="Enable OCR for scanned pages (default).",
    )
    ocr_group.add_argument(
        "--no-ocr",
        dest="enable_ocr",
        action="store_false",
        help="Disable OCR; faster for text-native PDFs.",
    )

    # -- Observability ---------------------------------------------------
    parser.add_argument(
        "--log-level",
        default=os.environ.get("PDF_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Console log level (default: INFO).",
    )
    parser.add_argument(
        "--checkpoint",
        default=".checkpoint.db",
        help="Path to the checkpoint file. "
             "*.db / *.sqlite → SQLite transactional checkpoint; "
             "anything else → legacy text file (default: .checkpoint.db).",
    )

    # -- Text cleaning ---------------------------------------------------
    cleaning_group = parser.add_mutually_exclusive_group()
    cleaning_group.add_argument(
        "--cleaning",
        dest="enable_cleaning",
        action="store_true",
        default=True,
        help="Enable the 4-layer text cleaning pipeline (default).",
    )
    cleaning_group.add_argument(
        "--no-cleaning",
        dest="enable_cleaning",
        action="store_false",
        help="Disable text cleaning; write raw Markdown only.",
    )
    parser.add_argument(
        "--no-repetition-filter",
        action="store_true",
        help="Disable the repetition-filter layer of text cleaning.",
    )
    parser.add_argument(
        "--no-ocr-correction",
        action="store_true",
        help="Disable the OCR-correction layer of text cleaning.",
    )
    parser.add_argument(
        "--no-paragraph-repair",
        action="store_true",
        help="Disable the paragraph-repair layer of text cleaning.",
    )

    # -- Memory control ------------------------------------------------
    parser.add_argument(
        "--max-memory-per-worker-mb",
        type=float,
        default=None,
        help="RSS + GPU memory limit per worker in MB. "
             "None = fallback to $PDF_PARSER_MAX_MEMORY_MB or 4096.",
    )
    parser.add_argument(
        "--global-memory-buffer-mb",
        type=float,
        default=2048,
        help="Minimum free system RAM (in MB) required before a new task "
             "is submitted (default: 2048).",
    )
    parser.add_argument(
        "--memory-check-interval",
        type=float,
        default=2.0,
        help="Worker memory-watchdog polling interval in seconds (default: 2).",
    )

    # -- Hooks -----------------------------------------------------------
    parser.add_argument(
        "--skip-dedup",
        action="store_true",
        help="Skip the LSH deduplication hook after batch completion.",
    )

    return parser


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _run_orchestrator(args: argparse.Namespace) -> list:
    """Stage 1: discover, schedule, and execute PDF parsing."""
    parser_config = {
        "enable_ocr": args.enable_ocr,
        "extract_images": True,
        "timeout_seconds": args.timeout,
        "output_dir": args.output_dir,
    }

    cleaning_config: dict[str, Any] | None = None
    if args.enable_cleaning:
        cleaning_config = {
            "repetition_filter": not args.no_repetition_filter,
            "ocr_corrector": not args.no_ocr_correction,
            "paragraph_repair": not args.no_paragraph_repair,
        }

    orchestrator = PipelineOrchestrator(
        parser_cls=MinerUParser,
        parser_config=parser_config,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        max_workers=args.workers,
        checkpoint_file=args.checkpoint,
        cleaning_config=cleaning_config,
        max_memory_per_worker_mb=args.max_memory_per_worker_mb,
        global_memory_buffer_mb=args.global_memory_buffer_mb,
        memory_check_interval=args.memory_check_interval,
    )

    return orchestrator.run()


def _persist_results(results: list, sink: DataSink) -> None:
    """Stage 2: route every ParseResult to the appropriate sink stream."""
    for result in results:
        sink.emit(result)

    logger.info(
        "Persistence complete — %d success, %d failure records",
        sink.success_count,
        sink.failure_count,
    )


def _run_post_processing(jsonl_path: Path, skip_dedup: bool) -> None:
    """Stage 3: execute post-processing hooks (deduplication, etc.)."""
    if skip_dedup:
        logger.info("Skipping deduplication hook (--skip-dedup).")
        return

    if not jsonl_path.exists():
        logger.warning("JSONL file not found, skipping deduplication: %s", jsonl_path)
        return

    run_deduplication(jsonl_path)


def _print_summary(results: list, sink: DataSink, elapsed_sec: float) -> None:
    """Print a human-readable summary to stdout."""
    success = sum(1 for r in results if r.status == Status.SUCCESS)
    failed = len(results) - success

    print("\n" + "=" * 60)
    print(" PDF Parsing Pipeline Summary")
    print("=" * 60)
    print(f"  Total processed (this run) : {len(results)}")
    print(f"  Success                    : {success}")
    print(f"  Failed                     : {failed}")
    print(f"  JSONL records (cumulative) : {sink.success_count}")
    print(f"  DLQ records   (cumulative) : {sink.failure_count}")
    print(f"  Wall-clock time            : {elapsed_sec:.2f}s")
    print(f"  Output directory           : {sink.output_dir}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Execute the full pipeline: parse → persist → post-process."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # 1. Configure structured logging
    configure_logging(level=getattr(logging, args.log_level.upper()))
    logger.info("Pipeline starting", extra={"args": vars(args)})

    # 2. Initialise the data sink (JSONL + dead-letter log)
    sink = DataSink(
        output_dir=args.output_dir,
        jsonl_filename="parsed_results.jsonl",
        failed_log_filename="failed_jobs.log",
    )

    import time
    t0 = time.perf_counter()

    with sink:
        # Stage 1 — Batch parsing with process parallelism
        results = _run_orchestrator(args)

        # Stage 2 — Write results to durable append-only files
        _persist_results(results, sink)

    # Stage 3 — Post-processing hooks (LSH deduplication, etc.)
    _run_post_processing(sink.jsonl_path, skip_dedup=args.skip_dedup)

    elapsed = time.perf_counter() - t0
    _print_summary(results, sink, elapsed)

    # Return exit code: 0 = all OK, 1 = at least one failure
    has_failures = any(r.status != Status.SUCCESS for r in results)
    return 1 if has_failures else 0


# ---------------------------------------------------------------------------
# Guard for multiprocessing safety on Windows (spawn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
