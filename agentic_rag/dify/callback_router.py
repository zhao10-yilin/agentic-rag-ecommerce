"""Async callback router — Dify webhook receiver for ASYNC_CALLBACK tools.

When a Dify workflow runs in ASYNC_CALLBACK mode, it returns immediately
with a task_id.  The Executor creates an ``asyncio.Event`` and registers
it in this router.  When the Dify webhook fires, the router resolves the
event, waking the waiting ``asyncio.Future`` in the Executor.

Flow:
    1. Executor calls Dify workflow (async, non-blocking) → gets task_id.
    2. Executor creates ``asyncio.Event``, stores in CallbackRouter keyed by task_id.
    3. Executor ``await event.wait()`` — suspends without consuming CPU.
    4. Dify workflow completes → POST /api/v1/callbacks/dify/{task_id}.
    5. CallbackRouter resolves the event → Executor wakes up, returns result.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CallbackEntry:
    """A pending async callback."""

    task_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    timeout_at: float = 0.0


class CallbackRouter:
    """Registry of pending async callbacks, keyed by task_id.

    Thread-safe for registration from asyncio tasks; webhook delivery
    is expected from a single asyncio event loop.

    Parameters
    ----------
    default_timeout:
        Maximum seconds to wait for a callback before timing out.
    max_pending:
        Hard cap on concurrent pending callbacks.
    """

    def __init__(
        self,
        *,
        default_timeout: float = 120.0,
        max_pending: int = 500,
    ) -> None:
        self._timeout = default_timeout
        self._max = max_pending
        self._pending: dict[str, CallbackEntry] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Executor side — register and wait
    # ------------------------------------------------------------------

    async def register(self, task_id: str) -> CallbackEntry:
        """Register a callback slot.  Raises RuntimeError if at capacity."""
        async with self._lock:
            if len(self._pending) >= self._max:
                raise RuntimeError(
                    f"CallbackRouter at capacity ({self._max}). Cannot register {task_id}."
                )
            entry = CallbackEntry(
                task_id=task_id,
                timeout_at=time.time() + self._timeout,
            )
            self._pending[task_id] = entry
            logger.info("Callback registered: %s (pending: %d)", task_id, len(self._pending))
            return entry

    async def wait(self, task_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Wait for a callback to arrive, or timeout.

        Returns the callback payload dict on success, or
        ``{"status": "timeout", "error": "..."}`` on timeout.
        """
        entry = self._pending.get(task_id)
        if entry is None:
            # Maybe already delivered — check if we have the result
            return {"status": "error", "error": f"No callback slot for {task_id}"}

        effective_timeout = timeout or self._timeout
        try:
            await asyncio.wait_for(entry.event.wait(), timeout=effective_timeout)
            logger.info("Callback resolved: %s", task_id)
            return entry.result
        except asyncio.TimeoutError:
            logger.warning("Callback timeout: %s after %.1fs", task_id, effective_timeout)
            async with self._lock:
                self._pending.pop(task_id, None)
            return {
                "status": "timeout",
                "error": f"Callback for {task_id} not received within {effective_timeout}s",
            }

    # ------------------------------------------------------------------
    # Webhook side — deliver callback
    # ------------------------------------------------------------------

    async def deliver(self, task_id: str, payload: dict[str, Any]) -> bool:
        """Deliver a webhook payload to a waiting Executor task.

        Returns True if a waiter was found and woken, False if the task_id
        is unknown (already timed out or never registered).
        """
        async with self._lock:
            entry = self._pending.pop(task_id, None)

        if entry is None:
            logger.warning("Callback for unknown task_id: %s", task_id)
            return False

        entry.result = payload
        entry.event.set()
        logger.info("Callback delivered: %s (keys: %s)", task_id, list(payload.keys())[:5])
        return True

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup_expired(self) -> int:
        """Remove and resolve (as timeout) all expired entries.  Returns count."""
        now = time.time()
        expired: list[str] = []
        async with self._lock:
            for task_id, entry in list(self._pending.items()):
                if now > entry.timeout_at:
                    expired.append(task_id)
            for task_id in expired:
                entry = self._pending.pop(task_id)
                entry.result = {"status": "timeout", "error": "Expired during cleanup"}
                entry.event.set()
        if expired:
            logger.info("Cleaned up %d expired callbacks", len(expired))
        return len(expired)

    @property
    async def pending_count(self) -> int:
        async with self._lock:
            return len(self._pending)
