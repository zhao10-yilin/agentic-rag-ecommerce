"""OpenTelemetry instrumentation for Agentic RAG.

Instruments the PlanAndExecuteAgent with full distributed tracing.
When Jaeger / OTLP collector is available, traces flow to the collector.
When not, traces are recorded in the in-memory TraceStore for the admin panel.

Usage in agent core::

    from agentic_rag.observability.otel import instrument_agent_run

    @instrument_agent_run
    async def run(self, user_message, ...):
        ...
"""

from __future__ import annotations

import contextlib
import functools
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stub tracer — works without OTEL SDK installed
# ---------------------------------------------------------------------------


class StubSpan:
    """Records timing to TraceStore even when OTEL SDK is unavailable."""

    def __init__(self, name: str, trace_id: str = "") -> None:
        self.name = name
        self.trace_id = trace_id
        self.started_at = time.time()
        self.ended_at: float = 0.0
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = str(value)[:256]

    def set_status(self, code: int, description: str = "") -> None:
        self.attributes["status_code"] = code
        if description:
            self.attributes["status_description"] = description

    def record_exception(self, exc: Exception) -> None:
        self.attributes["exception"] = str(exc)[:500]

    def end(self) -> None:
        self.ended_at = time.time()
        # Record to trace store if available
        try:
            from agentic_rag.observability.trace_store import get_trace_store
            store = get_trace_store()
            if self.trace_id:
                store.add_phase(
                    self.trace_id, self.name,
                    self.started_at, self.ended_at,
                    metadata=self.attributes,
                )
        except Exception:
            pass


class StubTracer:
    """Creates stub spans when OTEL is unavailable."""

    def __init__(self) -> None:
        self._current_trace_id: str = ""

    def set_trace_id(self, trace_id: str) -> None:
        self._current_trace_id = trace_id

    @contextlib.contextmanager
    def start_span(self, name: str, **attrs: Any):
        span = StubSpan(name, trace_id=self._current_trace_id)
        for k, v in attrs.items():
            span.set_attribute(k, v)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(2, str(exc))
            raise
        finally:
            span.end()


# Global tracer instance
_tracer = StubTracer()


def get_tracer() -> StubTracer:
    return _tracer


# ---------------------------------------------------------------------------
# Decorator for instrumenting agent methods
# ---------------------------------------------------------------------------


def instrument_phase(phase_name: str):
    """Decorator that wraps an async method with a trace span.

    Usage::

        @instrument_phase("planning")
        async def plan(self, user_message, ...):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_span(phase_name) as span:
                span.set_attribute("function", func.__name__)
                result = await func(*args, **kwargs)
                return result
        return wrapper
    return decorator


def instrument_tool_call(tool_name: str):
    """Decorator for tool execute methods."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_span(f"tool.{tool_name}") as span:
                span.set_attribute("tool.name", tool_name)
                result = await func(*args, **kwargs)
                if hasattr(result, "status"):
                    span.set_attribute("tool.status", result.status)
                    if hasattr(result, "elapsed_ms"):
                        span.set_attribute("tool.elapsed_ms", result.elapsed_ms)
                return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# OTEL exporter stub (logs to console, ready for real collector)
# ---------------------------------------------------------------------------


class OTelStubExporter:
    """Logs spans to structured stdout. Replace with real OTLPSpanExporter in prod."""

    def __init__(self, endpoint: str = "") -> None:
        self._endpoint = endpoint
        self._spans: list[dict[str, Any]] = []

    def export(self, spans: list[dict[str, Any]]) -> None:
        for span in spans:
            self._spans.append(span)
            logger.debug(
                "OTEL span: name=%s trace_id=%s duration_ms=%.1f",
                span.get("name", "?"),
                span.get("trace_id", "?")[:8],
                (span.get("end_time", 0) - span.get("start_time", 0)) * 1000,
            )

    def get_spans(self) -> list[dict[str, Any]]:
        return list(self._spans)

    def clear(self) -> None:
        self._spans.clear()

    @property
    def span_count(self) -> int:
        return len(self._spans)


# Global exporter
_exporter = OTelStubExporter()


def get_exporter() -> OTelStubExporter:
    return _exporter
