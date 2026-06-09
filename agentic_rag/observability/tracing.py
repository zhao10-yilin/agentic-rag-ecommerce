"""OpenTelemetry tracing — create spans for each agent phase.

Usage::

    from agentic_rag.observability.tracing import trace_phase

    async with trace_phase("planning", query=user_message) as span:
        plan = await planner.plan(user_message)
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Check if OpenTelemetry is available
# ---------------------------------------------------------------------------

_OTEL_AVAILABLE = False
try:
    from opentelemetry import trace  # noqa: F401
    from opentelemetry.sdk.trace import TracerProvider  # noqa: F401

    _OTEL_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Tracer wrapper (no-op when OTEL not installed)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """A span that does nothing."""

    async def __aenter__(self) -> "_NoOpSpan":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass


class AgentTracer:
    """Lightweight tracer wrapper.

    Parameters
    ----------
    service_name:
        Service name for the tracer.
    exporter_endpoint:
        OTLP exporter endpoint.  If empty, tracing is disabled.
    enabled:
        Force enable/disable.
    """

    def __init__(
        self,
        *,
        service_name: str = "agentic_rag",
        exporter_endpoint: str = "",
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled and _OTEL_AVAILABLE and bool(exporter_endpoint)
        self._tracer: Any = None

        if self._enabled:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource(attributes={SERVICE_NAME: service_name})
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(endpoint=exporter_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)

            self._tracer = trace.get_tracer(service_name)
            logger.info("OpenTelemetry tracing enabled: endpoint=%s", exporter_endpoint)
        else:
            logger.info("OpenTelemetry tracing disabled")

    @contextlib.asynccontextmanager
    async def trace_phase(
        self,
        phase: str,
        **attributes: Any,
    ) -> AsyncIterator[Any]:
        """Create a span for an agent phase.

        Usage::

            async with tracer.trace_phase("planning", query="hello") as span:
                ...
        """
        if not self._enabled or self._tracer is None:
            yield _NoOpSpan()
            return

        with self._tracer.start_as_current_span(f"agent.{phase}") as span:
            for k, v in attributes.items():
                span.set_attribute(k, str(v)[:256])
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                span.set_status({"status_code": 2})  # ERROR
                raise
