"""Client-side call deduplication for write operations.

When an internal API doesn't support idempotency (no ``Idempotency-Key``
header support), the Agent provides a client-side safety net.

Key design decisions:
1. **READ tools bypass dedup entirely** — they're naturally idempotent.
2. **Write results are cached by idempotency_key with TTL** — replaying
   a successful write returns the original result, not a duplicate mutation.
3. **Error results are NOT cached** — transient failures should be retried.
4. **This is best-effort, not transactional** — a network partition during
   response delivery can still cause duplicate writes.  For true exactly-once
   semantics, the server-side API must support idempotency keys.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from agentic_rag.models import ToolResult

logger = logging.getLogger(__name__)


class CallDeduplicationStore:
    """Thread-safe in-memory store for write-tool results.

    Parameters
    ----------
    ttl_seconds:
        How long to retain a cached result.  After TTL, the entry is
        eligible for eviction and a retry will re-execute.
    max_entries:
        Hard cap on stored entries (LRU eviction when exceeded).
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,  # 1 hour
        max_entries: int = 10_000,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: dict[str, _DedupEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has(self, idempotency_key: str) -> bool:
        """Check if a result exists and has not expired."""
        with self._lock:
            entry = self._store.get(idempotency_key)
            if entry is None:
                return False
            if entry.is_expired(self._ttl):
                del self._store[idempotency_key]
                return False
            return True

    def get(self, idempotency_key: str) -> ToolResult | None:
        """Return the cached result or None if missing/expired."""
        with self._lock:
            entry = self._store.get(idempotency_key)
            if entry is None:
                return None
            if entry.is_expired(self._ttl):
                del self._store[idempotency_key]
                return None
            # Move to end for LRU
            del self._store[idempotency_key]
            self._store[idempotency_key] = entry
            return entry.result

    def put(self, idempotency_key: str, result: ToolResult) -> None:
        """Cache a result.  Only caches success/degraded — errors are not stored
        so they can be retried."""
        if result.status in ("error", "timeout"):
            return  # Don't cache transient failures

        with self._lock:
            # Enforce capacity
            while len(self._store) >= self._max:
                oldest = min(self._store, key=lambda k: self._store[k].timestamp)
                del self._store[oldest]
                logger.debug("Dedup store evicted: %s", oldest)

            self._store[idempotency_key] = _DedupEntry(
                result=result,
                timestamp=time.time(),
            )

    def clear_expired(self) -> int:
        """Remove all expired entries.  Returns count removed."""
        with self._lock:
            expired = [
                k for k, e in self._store.items()
                if e.is_expired(self._ttl)
            ]
            for k in expired:
                del self._store[k]
            return len(expired)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)


class _DedupEntry:
    __slots__ = ("result", "timestamp")

    def __init__(self, result: ToolResult, timestamp: float) -> None:
        self.result = result
        self.timestamp = timestamp

    def is_expired(self, ttl: int) -> bool:
        return time.time() - self.timestamp > ttl
