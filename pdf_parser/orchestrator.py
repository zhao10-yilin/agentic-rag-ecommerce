"""Industrial-grade batch PDF parser orchestrator."""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pdf_parser.base import BasePDFParser
from pdf_parser.checkpoint import CheckpointManager
from pdf_parser.checkpoint_sqlite import TransactionalCheckpoint
from pdf_parser.cleaning import TextCleaner
from pdf_parser.exceptions import MemoryLimitExceeded, ParseTimeoutError
from pdf_parser.models import ParseResult, Status

logger = logging.getLogger(__name__)

P = TypeVar("P", bound=BasePDFParser)

MAX_HARD_KILL_RETRIES: int = 2


def _sha256_file(path: Path) -> str:
    """Return hex-digest SHA-256 of *path* (streaming, constant memory)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Memory watchdog (runs inside each worker process)
# ---------------------------------------------------------------------------


class _MemoryWatchdog:
    """Daemon thread that hard-kills the host process on memory overrun.

    This lives **inside** the worker child process.  It polls RSS (and GPU
    memory if torch.cuda is available) every *interval_seconds*.  When the
    combined footprint exceeds *limit_mb* it logs a critical message and calls
    ``os._exit(137)`` — an immediate, unclean termination that the parent
    detects as :class:`BrokenProcessPool`.
    """

    def __init__(
        self,
        *,
        limit_mb: float,
        interval_seconds: float = 2.0,
        file_id: str | None = None,
    ) -> None:
        self.limit_mb = limit_mb
        self.interval_seconds = interval_seconds
        self.file_id = file_id
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="memory_watchdog"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds + 1)

    def _loop(self) -> None:
        try:
            import psutil
        except ImportError:
            return

        proc = psutil.Process()
        while not self._stop_event.is_set():
            try:
                rss_mb = proc.memory_info().rss / (1024 * 1024)

                gpu_mb = 0.0
                try:
                    import torch

                    if torch.cuda.is_available():
                        gpu_mb = torch.cuda.memory_allocated() / (1024 * 1024)
                except Exception:
                    pass

                total_mb = rss_mb + gpu_mb
                if total_mb > self.limit_mb:
                    logger.critical(
                        "OOM watchdog triggered: RSS=%.1f MB GPU=%.1f MB "
                        "limit=%.1f MB — hard-killing worker",
                        rss_mb,
                        gpu_mb,
                        self.limit_mb,
                        extra={"file_id": self.file_id},
                    )
                    # 137 = exit code traditionally used by Linux OOM killer
                    os._exit(137)
            except Exception:
                pass
            self._stop_event.wait(self.interval_seconds)


# ---------------------------------------------------------------------------
# Worker function (must be top-level to be picklable by multiprocessing)
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),  # original + 2 retries
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(
        (ParseTimeoutError, OSError, RuntimeError, MemoryLimitExceeded)
    ),
    reraise=True,
)
def _parse_worker(
    parser_cls: type[P],
    config: dict[str, Any],
    file_path: str,
    file_id: str,
) -> ParseResult:
    """Instantiate a parser inside the child process and parse a single file.

    This function is decorated with ``tenacity`` so that transient failures
    (timeouts, temporary I/O errors, memory spikes) are retried with
    exponential backoff before the exception is finally propagated to the
    parent process.
    """
    # Extract watchdog configuration (orchestrator-injected internal keys)
    mem_limit = config.get("_watchdog_memory_limit_mb")
    interval = config.get("_watchdog_interval", 2.0)

    watchdog: _MemoryWatchdog | None = None
    if mem_limit:
        watchdog = _MemoryWatchdog(
            limit_mb=mem_limit,
            interval_seconds=interval,
            file_id=file_id,
        )
        watchdog.start()

    try:
        # Re-create the parser instance inside the worker process;
        # loggers and CUDA contexts cannot be pickled across process boundaries.
        parser = parser_cls(config=config)
        return parser.parse(file_path, file_id=file_id)
    finally:
        if watchdog:
            watchdog.stop()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """Batch PDF parser orchestrator with process-level parallelism.

    Features
    --------
    * **Multi-process execution** — uses :class:`ProcessPoolExecutor` so that
      CPU / GPU intensive model inference can saturate all available cores
      without GIL contention.
    * **Dynamic worker sizing** — ``max_workers`` is read from the config dict,
      falling back to the ``PDF_PARSER_WORKERS`` environment variable, and
      finally defaulting to ``os.cpu_count()``.
    * **Exponential-backoff retry** — transient errors (timeout, I/O) are
      retried up to 2 additional times via ``tenacity``.
    * **Crash-resumable checkpoint** — an append-only ``.processed_files`` file
      records every successful ``file_id``; on restart the orchestrator skips
      files already in the checkpoint.
    * **Graceful shutdown** — SIGINT / SIGTERM set an internal flag so that
      the loop exits cleanly and the executor shuts down without orphaning
      workers.
    * **OOM hard-kill** — a per-worker memory watchdog calls ``os._exit(137)``
      when RSS (+ GPU memory) exceeds a configurable limit.  The orchestrator
      detects the resulting :class:`BrokenProcessPool`, rebuilds the executor,
      and re-submits the affected task up to *MAX_HARD_KILL_RETRIES* times.
    * **Global memory gate** — before submitting a new task the orchestrator
      checks ``psutil.virtual_memory().available`` and pauses if the system
      is running low on RAM.

    .. warning::
        On Windows the default multiprocessing start method is ``spawn``.
        Therefore the module that instantiates ``PipelineOrchestrator`` **must**
        guard its entry point with ``if __name__ == "__main__":`` or the
        child processes will recursively re-import the parent script.
    """

    DEFAULT_MAX_WORKERS_ENV: str = "PDF_PARSER_WORKERS"
    DEFAULT_MAX_MEMORY_MB_ENV: str = "PDF_PARSER_MAX_MEMORY_MB"

    def __init__(
        self,
        parser_cls: type[P],
        parser_config: dict[str, Any] | None = None,
        *,
        input_dir: str | Path,
        output_dir: str | Path | None = None,
        max_workers: int | None = None,
        checkpoint_file: str | Path = ".processed_files",
        cleaning_config: dict[str, Any] | None = None,
        max_memory_per_worker_mb: float | None = None,
        global_memory_buffer_mb: float = 2048,
        memory_check_interval: float = 2.0,
    ) -> None:
        """Initialise the orchestrator.

        Args:
            parser_cls: Concrete parser strategy class (e.g. ``MinerUParser``).
            parser_config: Configuration dict forwarded to the parser constructor.
            input_dir: Directory containing PDF files to process.
            output_dir: Directory where extracted assets are persisted.
                If the parser config already contains ``output_dir``, that value
                takes precedence.
            max_workers: Number of worker processes.  ``None`` triggers auto-
                detection (env var → CPU count).
            checkpoint_file: Path to the checkpoint file.
            cleaning_config: Dict forwarded to :class:`TextCleaner`.
                Pass ``{"rule_cleaner": False, ...}`` to disable layers.
            max_memory_per_worker_mb: RSS + GPU memory limit per worker in MB.
                ``None`` falls back to ``$PDF_PARSER_MAX_MEMORY_MB`` or 4096.
            global_memory_buffer_mb: Minimum free system RAM (in MB) required
                before a new task is submitted.
            memory_check_interval: Polling interval for the worker watchdog in
                seconds.
        """
        self._parser_cls = parser_cls
        self._parser_config = dict(parser_config or {})
        self._input_dir = Path(input_dir).expanduser().resolve()
        self._output_dir: Path | None = (
            Path(output_dir).expanduser().resolve() if output_dir else None
        )
        self._max_workers = self._resolve_max_workers(max_workers)
        self._checkpoint = self._build_checkpoint(checkpoint_file)
        self._cleaner = TextCleaner(config=cleaning_config)

        # Memory control
        self._max_memory_per_worker_mb = max_memory_per_worker_mb or float(
            os.environ.get(self.DEFAULT_MAX_MEMORY_MB_ENV, "4096")
        )
        self._global_memory_buffer_mb = global_memory_buffer_mb
        self._memory_check_interval = memory_check_interval
        self._psutil_available = self._check_psutil()

        self._shutdown_requested = False
        self._signal_fired = False
        self._executor: ProcessPoolExecutor | None = None

        # Install POSIX / Windows signal handlers for graceful teardown
        self._install_signal_handlers()

        logger.info(
            "PipelineOrchestrator initialised",
            extra={
                "parser": parser_cls.__name__,
                "input_dir": str(self._input_dir),
                "output_dir": str(self._output_dir) if self._output_dir else None,
                "max_workers": self._max_workers,
                "max_memory_per_worker_mb": self._max_memory_per_worker_mb,
                "global_memory_buffer_mb": self._global_memory_buffer_mb,
                "checkpoint": str(self._checkpoint.checkpoint_file),
                "already_processed": self._checkpoint.processed_count,
            },
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> list[ParseResult]:
        """Execute the batch pipeline.

        Returns:
            A list of :class:`ParseResult` objects (one per submitted file).
            Successfully processed files are recorded in the checkpoint.
        """
        pdf_files = self._collect_pdf_files()
        pending = self._checkpoint.filter_pending(pdf_files)

        if not pending:
            logger.info("No pending PDF files found in %s", self._input_dir)
            return []

        total = len(pending) + self._checkpoint.processed_count
        logger.info(
            "Starting batch processing: %d pending / %d total",
            len(pending),
            total,
        )

        results: list[ParseResult] = []
        completed = 0
        failed = 0
        hard_kill_retries: dict[str, int] = {}

        while pending and not self._shutdown_requested:
            config = self._build_parser_config()

            executor = self._create_executor()
            self._executor = executor

            future_to_path: dict = {}
            batch_submitted: list[Path] = []

            # ---- Submit phase --------------------------------------------
            for path in pending[:]:
                if self._shutdown_requested:
                    logger.warning("Shutdown requested; stopping task submission")
                    break

                if not self._has_enough_memory():
                    logger.warning(
                        "Global memory buffer low (<%d MB available), "
                        "pausing submission",
                        self._global_memory_buffer_mb,
                    )
                    break

                file_id = path.stem
                # Transactional backend: claim the file atomically before submitting
                if isinstance(self._checkpoint, TransactionalCheckpoint):
                    file_hash = _sha256_file(path)
                    if not self._checkpoint.start(file_id, file_hash):
                        continue

                future = executor.submit(
                    _parse_worker,
                    self._parser_cls,
                    config,
                    str(path),
                    file_id,
                )
                future_to_path[future] = path
                batch_submitted.append(path)

            for path in batch_submitted:
                pending.remove(path)

            # ---- Collect phase -------------------------------------------
            broken_pool = False
            try:
                for future in as_completed(future_to_path):
                    if self._shutdown_requested:
                        break

                    path = future_to_path[future]
                    try:
                        result: ParseResult = future.result()
                    except BrokenProcessPool:
                        broken_pool = True
                        logger.error(
                            "Process pool broken — worker hard-killed "
                            "(likely OOM). Rebuilding pool and re-queueing "
                            "unfinished tasks."
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        file_id = path.stem
                        err = f"Worker crash ({type(exc).__name__}): {exc}"
                        self._record_failure(file_id, err)
                        logger.exception(
                            "[%d/%d] CRASH    %s  exception=%s",
                            completed
                            + failed
                            + self._checkpoint.processed_count
                            - 1,
                            total,
                            file_id,
                            type(exc).__name__,
                        )
                        results.append(
                            ParseResult.build_failure(
                                file_id=file_id,
                                error_msg=err,
                                parser_name=self._parser_cls.__name__,
                            )
                        )
                        continue

                    results.append(result)

                    if result.status == Status.SUCCESS:
                        cleaned = self._cleaner.clean(result.markdown_content)
                        if cleaned != result.markdown_content:
                            result = result.model_copy(
                                update={"cleaned_markdown": cleaned}
                            )
                        self._record_success(result.file_id)
                        completed += 1
                        logger.info(
                            "[%d/%d] SUCCESS  %s  (%.2fs, %d pages, %d imgs)",
                            completed
                            + failed
                            + self._checkpoint.processed_count
                            - 1,
                            total,
                            result.file_id,
                            result.metrics.elapsed_seconds
                            if result.metrics
                            else 0.0,
                            result.metrics.page_count if result.metrics else 0,
                            result.metrics.image_count
                            if result.metrics
                            else 0,
                        )
                    else:
                        self._record_failure(result.file_id, result.error_msg)
                        failed += 1
                        logger.error(
                            "[%d/%d] FAILED   %s  error=%s",
                            completed
                            + failed
                            + self._checkpoint.processed_count
                            - 1,
                            total,
                            result.file_id,
                            result.error_msg,
                        )

            finally:
                executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

            # ---- Re-queue after broken pool ------------------------------
            if broken_pool:
                for future, path in future_to_path.items():
                    if future.done():
                        continue
                    file_id = path.stem
                    hard_kill_retries[file_id] = (
                        hard_kill_retries.get(file_id, 0) + 1
                    )
                    if hard_kill_retries[file_id] <= MAX_HARD_KILL_RETRIES:
                        pending.insert(0, path)
                        logger.warning(
                            "Re-queueing %s after hard-kill (retry %d/%d)",
                            file_id,
                            hard_kill_retries[file_id],
                            MAX_HARD_KILL_RETRIES,
                        )
                    else:
                        failed += 1
                        err = (
                            f"Worker hard-killed (OOM) after "
                            f"{MAX_HARD_KILL_RETRIES} retries — treating as "
                            f"permanent failure"
                        )
                        self._record_failure(file_id, err)
                        results.append(
                            ParseResult.build_failure(
                                file_id=file_id,
                                error_msg=err,
                                parser_name=self._parser_cls.__name__,
                            )
                        )
                        logger.error(
                            "[%d/%d] OOM-KILL %s  retries exhausted",
                            completed
                            + failed
                            + self._checkpoint.processed_count
                            - 1,
                            total,
                            file_id,
                        )

        logger.info(
            "Batch run complete: %d succeeded, %d failed, %d already processed",
            completed,
            failed,
            self._checkpoint.processed_count - completed,
        )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_pdf_files(self) -> list[Path]:
        """Return all ``*.pdf`` files inside :attr:`_input_dir`, sorted."""
        if not self._input_dir.is_dir():
            raise NotADirectoryError(
                f"Input directory does not exist: {self._input_dir}"
            )

        files = sorted(
            p
            for p in self._input_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"
        )
        logger.info("Discovered %d PDF files in %s", len(files), self._input_dir)
        return files

    def _resolve_max_workers(self, explicit: int | None) -> int:
        """Determine the number of worker processes."""
        if explicit is not None and explicit > 0:
            return explicit

        env_val = os.environ.get(self.DEFAULT_MAX_WORKERS_ENV)
        if env_val is not None:
            try:
                parsed = int(env_val)
                if parsed > 0:
                    return parsed
            except ValueError:
                logger.warning(
                    "Ignoring non-integer %s=%s",
                    self.DEFAULT_MAX_WORKERS_ENV,
                    env_val,
                )

        return os.cpu_count() or 1

    @staticmethod
    def _build_checkpoint(
        checkpoint_file: str | Path,
    ) -> CheckpointManager | TransactionalCheckpoint:
        """Return the appropriate checkpoint backend based on file extension."""
        suffix = Path(checkpoint_file).suffix.lower()
        if suffix in (".db", ".sqlite", ".sqlite3"):
            return TransactionalCheckpoint(checkpoint_file)
        return CheckpointManager(checkpoint_file)

    def _record_success(self, file_id: str) -> None:
        if isinstance(self._checkpoint, TransactionalCheckpoint):
            out = str(self._output_dir) if self._output_dir else None
            self._checkpoint.complete(file_id, status="success", output_dir=out)
        else:
            self._checkpoint.add(file_id)

    def _record_failure(self, file_id: str, error_msg: str | None = None) -> None:
        if isinstance(self._checkpoint, TransactionalCheckpoint):
            self._checkpoint.complete(file_id, status="failed", error_msg=error_msg)
        # Legacy text checkpoint has no failure tracking

    def _build_parser_config(self) -> dict[str, Any]:
        """Merge orchestrator-level defaults into the parser config."""
        config = dict(self._parser_config)
        if self._output_dir and "output_dir" not in config:
            config["output_dir"] = str(self._output_dir)
        if self._max_memory_per_worker_mb:
            config["_watchdog_memory_limit_mb"] = self._max_memory_per_worker_mb
            config["_watchdog_interval"] = self._memory_check_interval
        return config

    def _create_executor(self) -> ProcessPoolExecutor:
        """Build a :class:`ProcessPoolExecutor` with memory-safe defaults."""
        kwargs: dict[str, Any] = {"max_workers": self._max_workers}
        if sys.version_info >= (3, 11):
            # Force a fresh process after every task so that C / CUDA memory
            # leaks are never accumulated across submissions.
            kwargs["max_tasks_per_child"] = 1
        return ProcessPoolExecutor(**kwargs)

    @staticmethod
    def _check_psutil() -> bool:
        """Return ``True`` if ``psutil`` is installed."""
        try:
            import psutil  # noqa: F401

            return True
        except ImportError:
            logger.warning(
                "psutil is not installed; global memory gate disabled. "
                "Install it for OOM protection: pip install psutil"
            )
            return False

    def _has_enough_memory(self) -> bool:
        """Return ``True`` if system free RAM is above the safety buffer."""
        if not self._psutil_available:
            return True
        try:
            import psutil

            available_mb = psutil.virtual_memory().available / (1024 * 1024)
            return available_mb > self._global_memory_buffer_mb
        except Exception:
            return True

    def _install_signal_handlers(self) -> None:
        """Register SIGINT / SIGTERM / SIGBREAK handlers for graceful shutdown."""

        def _handler(signum: int, _frame: Any) -> None:
            if self._signal_fired:
                return
            self._signal_fired = True

            sig_name = signal.Signals(signum).name
            logger.warning("Received %s, requesting graceful shutdown…", sig_name)
            self._shutdown_requested = True

            if self._executor is not None:
                try:
                    self._executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    # cancel_futures requires Python >= 3.9
                    self._executor.shutdown(wait=False)

        signal.signal(signal.SIGINT, _handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handler)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handler)
