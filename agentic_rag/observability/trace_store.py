"""In-memory trace store — records full Agent execution traces for the Trace Viewer.

Each trace captures: plan steps, tool calls with timing/status, reflection
rounds, and the final synthesis.  Used by the Streamlit admin panel to
render Gantt charts and dependency graphs.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentic_rag.models import AgentPlan, AgentState, AgentStep, ToolCallRecord


@dataclass
class PhaseSpan:
    """One phase in an Agent trace."""
    phase: str          # planning, validating, executing_step_0, reflecting, synthesizing
    started_at: float
    ended_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at) * 1000 if self.ended_at else 0


@dataclass
class AgentTrace:
    """Complete trace of a single Agent run."""
    trace_id: str
    user_message: str
    started_at: float
    ended_at: float = 0.0
    phases: list[PhaseSpan] = field(default_factory=list)
    plan: AgentPlan | None = None
    tool_records: list[ToolCallRecord] = field(default_factory=list)
    final_state: str = ""
    reflection_rounds: int = 0
    degradation_notes: list[str] = field(default_factory=list)
    experiment_group: str = "control"

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at) * 1000 if self.ended_at else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "user_message": self.user_message[:200],
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "phases": [
                {"phase": p.phase, "duration_ms": p.duration_ms, "metadata": p.metadata}
                for p in self.phases
            ],
            "plan_steps": [
                {
                    "step_index": s.step_index,
                    "description": s.description,
                    "actions": [a.tool_name for a in s.actions],
                    "depends_on": s.depends_on,
                }
                for s in (self.plan.steps if self.plan else [])
            ],
            "tool_records": [
                {
                    "tool_name": r.tool_name,
                    "status": r.result.status,
                    "summary": r.result.summary[:200],
                    "elapsed_ms": r.result.elapsed_ms,
                    "step_index": r.step_index,
                    "error": r.result.error,
                }
                for r in self.tool_records
            ],
            "reflection_rounds": self.reflection_rounds,
            "final_state": self.final_state,
            "degradation_notes": self.degradation_notes,
            "experiment_group": self.experiment_group,
        }


class TraceStore:
    """Thread-safe ring buffer for Agent traces.

    Parameters
    ----------
    max_traces:
        Maximum number of traces to retain (FIFO eviction).
    """

    def __init__(self, max_traces: int = 500) -> None:
        self._max = max_traces
        self._traces: list[AgentTrace] = []
        self._lock = threading.Lock()

    def start_trace(self, user_message: str, experiment_group: str = "control") -> str:
        """Begin a new trace. Returns the trace_id."""
        trace_id = uuid.uuid4().hex[:16]
        trace = AgentTrace(
            trace_id=trace_id,
            user_message=user_message,
            started_at=time.time(),
            experiment_group=experiment_group,
        )
        with self._lock:
            self._traces.append(trace)
            while len(self._traces) > self._max:
                self._traces.pop(0)
        return trace_id

    def add_phase(self, trace_id: str, phase: str, started_at: float,
                  ended_at: float = 0.0, metadata: dict[str, Any] | None = None) -> None:
        """Record a phase span in an existing trace."""
        with self._lock:
            for t in self._traces:
                if t.trace_id == trace_id:
                    t.phases.append(PhaseSpan(
                        phase=phase, started_at=started_at,
                        ended_at=ended_at or time.time(),
                        metadata=metadata or {},
                    ))
                    return

    def complete_trace(self, trace_id: str, plan: AgentPlan | None = None,
                       records: list[ToolCallRecord] | None = None,
                       final_state: str = "done", reflection_rounds: int = 0,
                       degradation_notes: list[str] | None = None) -> None:
        """Finalize a trace with execution results."""
        with self._lock:
            for t in self._traces:
                if t.trace_id == trace_id:
                    t.ended_at = time.time()
                    t.plan = plan
                    t.tool_records = records or []
                    t.final_state = final_state
                    t.reflection_rounds = reflection_rounds
                    t.degradation_notes = degradation_notes or []
                    return

    def get_trace(self, trace_id: str) -> AgentTrace | None:
        with self._lock:
            for t in self._traces:
                if t.trace_id == trace_id:
                    return t
        return None

    def get_recent(self, n: int = 20) -> list[AgentTrace]:
        with self._lock:
            return list(reversed(self._traces[-n:]))

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._traces)

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """Compute aggregate metrics from stored traces."""
        with self._lock:
            if not self._traces:
                return {}

            durations = [t.duration_ms for t in self._traces if t.duration_ms > 0]
            durations.sort()

            total = len(self._traces)
            success = sum(1 for t in self._traces if t.final_state == "done")
            errors = sum(1 for t in self._traces if t.final_state == "error")
            clarifications = sum(
                1 for t in self._traces
                if any(p.phase == "clarifying" for p in t.phases)
            )

            tool_stats: dict[str, dict[str, int]] = {}
            for t in self._traces:
                for r in t.tool_records:
                    if r.tool_name not in tool_stats:
                        tool_stats[r.tool_name] = {"success": 0, "degraded": 0, "error": 0, "timeout": 0}
                    tool_stats[r.tool_name][r.result.status] += 1

            return {
                "total_traces": total,
                "success_rate": success / max(total, 1),
                "error_rate": errors / max(total, 1),
                "clarification_rate": clarifications / max(total, 1),
                "p50_ms": durations[len(durations) // 2] if durations else 0,
                "p99_ms": durations[int(len(durations) * 0.99)] if len(durations) >= 100 else (durations[-1] if durations else 0),
                "avg_duration_ms": sum(durations) / max(len(durations), 1),
                "tool_stats": {
                    name: {
                        "success_rate": stats["success"] / max(sum(stats.values()), 1),
                        **stats,
                    }
                    for name, stats in tool_stats.items()
                },
            }


# Global singleton
_trace_store: TraceStore | None = None


def get_trace_store() -> TraceStore:
    global _trace_store
    if _trace_store is None:
        _trace_store = TraceStore()
    return _trace_store
