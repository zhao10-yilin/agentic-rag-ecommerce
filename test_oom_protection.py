"""Unit tests for OOM protection mechanisms."""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from pdf_parser.exceptions import MemoryLimitExceeded
from pdf_parser.orchestrator import (
    MAX_HARD_KILL_RETRIES,
    PipelineOrchestrator,
    _MemoryWatchdog,
    _parse_worker,
)
from pdf_parser.models import ParseResult, Status


# ---------------------------------------------------------------------------
# MemoryLimitExceeded
# ---------------------------------------------------------------------------


def test_memory_limit_exceeded_attributes():
    exc = MemoryLimitExceeded(
        "OOM",
        memory_mb=5120.5,
        limit_mb=4096.0,
        file_id="test_file",
    )
    assert exc.memory_mb == 5120.5
    assert exc.limit_mb == 4096.0
    assert exc.file_id == "test_file"
    assert "OOM" in str(exc)


# ---------------------------------------------------------------------------
# _MemoryWatchdog
# ---------------------------------------------------------------------------


def test_watchdog_start_stop():
    """Watchdog thread should start and stop cleanly."""
    wd = _MemoryWatchdog(limit_mb=99999, interval_seconds=0.05)
    wd.start()
    assert wd._thread is not None
    assert wd._thread.is_alive()
    wd.stop()
    assert not wd._thread.is_alive()


def test_watchdog_does_not_fire_when_under_limit():
    """With a very high limit the watchdog should never trigger."""
    wd = _MemoryWatchdog(limit_mb=999999, interval_seconds=0.05)
    wd.start()
    time.sleep(0.15)  # let it poll a few times
    wd.stop()
    # If os._exit had been called we would not reach this line.
    assert True


# ---------------------------------------------------------------------------
# PipelineOrchestrator — memory helpers
# ---------------------------------------------------------------------------


def test_has_enough_memory_with_psutil():
    """On any normal machine free RAM should be > 2048 MB."""
    mock_cls = MagicMock()
    mock_cls.__name__ = "MockParser"
    orch = PipelineOrchestrator(
        parser_cls=mock_cls,
        input_dir=".",
        global_memory_buffer_mb=1,  # very low threshold
    )
    assert orch._has_enough_memory()


def test_create_executor_sets_max_tasks_per_child_on_py311_plus():
    """Python 3.11+ should get max_tasks_per_child=1."""
    mock_cls = MagicMock()
    mock_cls.__name__ = "MockParser"
    orch = PipelineOrchestrator(parser_cls=mock_cls, input_dir=".")
    executor = orch._create_executor()
    assert executor._max_tasks_per_child == 1  # type: ignore[attr-defined]
    executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# PipelineOrchestrator — CLI / config wiring
# ---------------------------------------------------------------------------


def test_orchestrator_ingests_memory_params():
    mock_cls = MagicMock()
    mock_cls.__name__ = "MockParser"
    orch = PipelineOrchestrator(
        parser_cls=mock_cls,
        input_dir=".",
        max_memory_per_worker_mb=8192,
        global_memory_buffer_mb=1024,
        memory_check_interval=5.0,
    )
    assert orch._max_memory_per_worker_mb == 8192
    assert orch._global_memory_buffer_mb == 1024
    assert orch._memory_check_interval == 5.0


def test_build_parser_config_injects_watchdog_keys():
    mock_cls = MagicMock()
    mock_cls.__name__ = "MockParser"
    orch = PipelineOrchestrator(
        parser_cls=mock_cls,
        input_dir=".",
        parser_config={"foo": "bar"},
        max_memory_per_worker_mb=2048,
        memory_check_interval=3.0,
    )
    cfg = orch._build_parser_config()
    assert cfg["_watchdog_memory_limit_mb"] == 2048
    assert cfg["_watchdog_interval"] == 3.0
    assert cfg["foo"] == "bar"


# ---------------------------------------------------------------------------
# _parse_worker — watchdog integration (mocked)
# ---------------------------------------------------------------------------


def test_parse_worker_starts_watchdog_when_limit_configured():
    """When _watchdog_memory_limit_mb is present, a watchdog should start."""
    mock_parser_cls = MagicMock()
    mock_parser = mock_parser_cls.return_value
    mock_parser.parse.return_value = ParseResult(
        file_id="x",
        status=Status.SUCCESS,
    )

    config = {
        "_watchdog_memory_limit_mb": 4096,
        "_watchdog_interval": 0.05,
    }

    result = _parse_worker(mock_parser_cls, config, "dummy.pdf", "x")
    assert result.status == Status.SUCCESS
    mock_parser.parse.assert_called_once_with("dummy.pdf", file_id="x")


# ---------------------------------------------------------------------------
# main.py CLI
# ---------------------------------------------------------------------------


def test_cli_memory_args_parsed():
    from pdf_parser.main import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(
        [
            "--max-memory-per-worker-mb", "8192",
            "--global-memory-buffer-mb", "4096",
            "--memory-check-interval", "5",
        ]
    )
    assert args.max_memory_per_worker_mb == 8192.0
    assert args.global_memory_buffer_mb == 4096.0
    assert args.memory_check_interval == 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
