"""Prometheus metrics for the agentic RAG system.

Exposes counters and histograms for observability and business KPIs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest  # noqa: F401

    _PROMETHEUS_AVAILABLE = True
except ImportError:
    pass


class AgentMetrics:
    """Prometheus metrics for agent performance and business KPIs.

    All metrics are no-ops if ``prometheus_client`` is not installed.
    """

    def __init__(self) -> None:
        self._enabled = _PROMETHEUS_AVAILABLE

        if self._enabled:
            from prometheus_client import Counter, Gauge, Histogram

            self._tool_calls = Counter(
                "agentic_rag_tool_calls_total",
                "Total tool calls",
                ["tool_name", "status"],
            )
            self._tool_latency = Histogram(
                "agentic_rag_tool_latency_seconds",
                "Tool execution latency",
                ["tool_name"],
                buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
            )
            self._agent_phases = Histogram(
                "agentic_rag_phase_duration_seconds",
                "Duration of each agent phase",
                ["phase"],
                buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
            )
            self._reflection_rounds = Counter(
                "agentic_rag_reflection_rounds_total",
                "Total reflection rounds",
            )
            self._plan_steps = Histogram(
                "agentic_rag_plan_steps",
                "Number of steps in generated plans",
                buckets=[1, 2, 3, 4, 5, 7, 10],
            )
            self._clarifications = Counter(
                "agentic_rag_clarifications_total",
                "Total clarification questions asked",
                ["intent"],
            )
            # Business KPIs
            self._conversion_rate = Gauge(
                "agentic_rag_business_conversion_rate",
                "Order conversion rate (estimated)",
            )
            self._first_contact_resolution = Gauge(
                "agentic_rag_business_first_contact_resolution",
                "First-contact resolution rate",
            )

            logger.info("Prometheus metrics enabled")

    # ------------------------------------------------------------------
    # Tool metrics
    # ------------------------------------------------------------------

    def record_tool_call(self, tool_name: str, status: str, elapsed_ms: float) -> None:
        if not self._enabled:
            return
        self._tool_calls.labels(tool_name=tool_name, status=status).inc()
        self._tool_latency.labels(tool_name=tool_name).observe(elapsed_ms / 1000.0)

    # ------------------------------------------------------------------
    # Phase metrics
    # ------------------------------------------------------------------

    def record_phase(self, phase: str, elapsed_ms: float) -> None:
        if not self._enabled:
            return
        self._agent_phases.labels(phase=phase).observe(elapsed_ms / 1000.0)

    # ------------------------------------------------------------------
    # Agent metrics
    # ------------------------------------------------------------------

    def record_reflection(self) -> None:
        if not self._enabled:
            return
        self._reflection_rounds.inc()

    def record_plan_steps(self, num_steps: int) -> None:
        if not self._enabled:
            return
        self._plan_steps.observe(num_steps)

    def record_clarification(self, intent: str) -> None:
        if not self._enabled:
            return
        self._clarifications.labels(intent=intent).inc()

    # ------------------------------------------------------------------
    # Business KPIs
    # ------------------------------------------------------------------

    def set_conversion_rate(self, rate: float) -> None:
        if not self._enabled:
            return
        self._conversion_rate.set(rate)

    def set_first_contact_resolution(self, rate: float) -> None:
        if not self._enabled:
            return
        self._first_contact_resolution.set(rate)

    # ------------------------------------------------------------------
    # HTTP endpoint
    # ------------------------------------------------------------------

    def get_metrics_response(self) -> tuple[bytes, str]:
        """Return Prometheus text format response (content, content_type)."""
        if not self._enabled:
            return b"# Prometheus metrics disabled\n", "text/plain"
        from prometheus_client import generate_latest

        return generate_latest(), "text/plain; version=0.0.4"
