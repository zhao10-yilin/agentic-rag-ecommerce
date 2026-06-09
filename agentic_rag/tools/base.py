"""Base tool abstractions — ToolResult, BaseTool, ToolRegistry.

Every tool the agent can invoke inherits from :class:`BaseTool` and is
registered in the :class:`ToolRegistry`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentic_rag.models import DegradationPolicy, ToolCall, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Execution profile — tells Planner and Executor what to expect
# ---------------------------------------------------------------------------


class ExecutionMode(str, Enum):
    SYNC = "sync"            # Returns result immediately (< 2s)
    ASYNC_POLL = "async_poll"  # Returns task_id, needs polling
    ASYNC_CALLBACK = "async_callback"  # Result arrives via webhook


class MutationKind(str, Enum):
    """Classifies a tool's side-effect profile for idempotency decisions."""
    READ = "read"          # No side effects — naturally idempotent
    CREATE = "create"       # Creates a new resource — needs dedup
    UPDATE = "update"       # Mutates existing resource — needs dedup
    DELETE = "delete"       # Destructive — needs dedup + confirmation


@dataclass
class ExecutionProfile:
    """Describes the temporal and side-effect characteristics of a tool.

    Parameters
    ----------
    mode:
        SYNC / ASYNC_POLL / ASYNC_CALLBACK.
    expected_latency_ms:
        P50 latency in milliseconds.
    max_latency_ms:
        P99 / worst-case latency.  Used by Executor as the timeout ceiling.
    polling_interval_ms:
        For ASYNC_POLL mode — how often to check for result completion.
    blocking:
        If True, the Planner MUST wait for this tool before starting the
        next step that depends on it.
    mutation_kind:
        Side-effect classification.  READ tools skip the dedup store.
        CREATE/UPDATE/DELETE tools are checked against the dedup store
        before execution.
    """

    mode: ExecutionMode = ExecutionMode.SYNC
    expected_latency_ms: int = 200
    max_latency_ms: int = 30_000
    polling_interval_ms: int = 1000
    blocking: bool = True
    mutation_kind: MutationKind = MutationKind.READ

    # Convenience presets
    @classmethod
    def instant(cls) -> "ExecutionProfile":
        return cls(mode=ExecutionMode.SYNC, expected_latency_ms=50, max_latency_ms=5_000)

    @classmethod
    def fast(cls) -> "ExecutionProfile":
        return cls(mode=ExecutionMode.SYNC, expected_latency_ms=200, max_latency_ms=15_000)

    @classmethod
    def slow(cls) -> "ExecutionProfile":
        return cls(mode=ExecutionMode.ASYNC_POLL, expected_latency_ms=8_000, max_latency_ms=60_000, polling_interval_ms=2000)

    @classmethod
    def very_slow(cls) -> "ExecutionProfile":
        return cls(mode=ExecutionMode.ASYNC_POLL, expected_latency_ms=30_000, max_latency_ms=180_000, polling_interval_ms=5000)


# ---------------------------------------------------------------------------
# BaseTool
# ---------------------------------------------------------------------------


class BaseTool(ABC):
    """Abstract base for all agent-invokable tools.

    Parameters
    ----------
    name:
        Unique tool identifier (e.g. ``"rag_search"``).
    description:
        Human-readable description for the LLM planner.  Should explain
        *what* the tool does, *when* to use it, and what the input fields mean.
    parameters:
        JSON Schema dict describing the tool's input shape.
    degradation:
        Fallback behaviour when the tool encounters an error.
    cache_ttl_seconds:
        TTL for cached results (only meaningful when degradation is
        ``RETURN_CACHED``).
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        degradation: DegradationPolicy = DegradationPolicy.FAIL_FAST,
        cache_ttl_seconds: int = 300,
        execution_profile: ExecutionProfile | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.degradation = degradation
        self.cache_ttl_seconds = cache_ttl_seconds
        self.execution_profile = execution_profile or ExecutionProfile.fast()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute the tool and return a standardised result."""
        ...

    def validate_input(self, input_data: dict[str, Any]) -> list[str]:
        """Validate *input_data* against this tool's JSON Schema.

        Returns a list of error messages (empty = valid).
        """
        errors: list[str] = []
        required = self.parameters.get("required", [])
        properties = self.parameters.get("properties", {})

        for field in required:
            if field not in input_data or input_data[field] is None:
                errors.append(f"Missing required field: {field}")

        for field, value in input_data.items():
            if field not in properties:
                continue
            prop = properties[field]
            expected_type = prop.get("type")
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"Field '{field}' expected string, got {type(value).__name__}")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"Field '{field}' expected number, got {type(value).__name__}")
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(f"Field '{field}' expected integer, got {type(value).__name__}")
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(f"Field '{field}' expected boolean, got {type(value).__name__}")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"Field '{field}' expected array, got {type(value).__name__}")

        return errors

    def to_openai_function(self) -> dict[str, Any]:
        """Return an OpenAI function-calling compatible schema dict.

        Appends execution profile hints to the description so the Planner
        can make intelligent scheduling decisions.
        """
        ep = self.execution_profile
        latency_hint = f" [延迟: ~{ep.expected_latency_ms}ms"
        if ep.mode != ExecutionMode.SYNC:
            latency_hint += f", 异步轮询模式, 间隔{ep.polling_interval_ms}ms"
        latency_hint += "]"

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description + latency_hint,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Thread-safe registry of all available tools.

    Tools are registered once at startup and looked up by name during
    plan validation and execution.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Add a tool.  Raises ``ValueError`` if the name is already taken."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s (degradation=%s)", tool.name, tool.degradation.value)

    def register_many(self, *tools: BaseTool) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_required(self, name: str) -> BaseTool:
        """Like ``get`` but raises ``KeyError`` if not found."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' is not registered. Available: {self.list_names()}")
        return tool

    def get_all_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI function schemas for all registered tools."""
        return [t.to_openai_function() for t in self._tools.values()]

    def list_names(self) -> list[str]:
        return sorted(self._tools)

    def list_descriptions(self) -> str:
        """Return a compact text listing for the planner prompt."""
        lines: list[str] = []
        for t in self._tools.values():
            required = t.parameters.get("required", [])
            props = t.parameters.get("properties", {})
            args = ", ".join(
                f"{k}: {props[k].get('type', 'any')}" for k in required
            )
            lines.append(f"- {t.name}({args}): {t.description}")
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
