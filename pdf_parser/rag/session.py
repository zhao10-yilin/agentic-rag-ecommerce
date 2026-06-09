"""Conversation session manager for multi-turn RAG chat.

Each session tracks:

* Full conversation history (user messages + assistant responses)
* Previously retrieved chunks (so follow-up questions can reference them)
* Last active timestamp (for auto-expiry of stale sessions)

Usage::

    mgr = SessionManager(ttl_seconds=3600)
    session = mgr.get_or_create("session_abc")
    session.add_turn(role="user", content="什么是违约责任")
    history = session.format_for_llm()  # returns list of {"role": ..., "content": ...}
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default max conversation turns to keep (older turns are trimmed)
MAX_HISTORY_TURNS = 20


@dataclass
class ChatSession:
    """A single multi-turn conversation."""

    session_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Accumulated chunk IDs across all turns — used to detect repeated topics
    seen_chunk_ids: set[str] = field(default_factory=set)

    def add_turn(
        self,
        *,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record a message in the conversation history."""
        msg: dict[str, Any] = {"role": role, "content": content}
        if sources:
            msg["sources"] = sources
        self.messages.append(msg)
        self.last_active = time.time()

        # Trim old history to prevent unbounded growth
        while len(self.messages) > MAX_HISTORY_TURNS * 2:  # user + assistant pairs
            self.messages.pop(0)

    def track_chunks(self, chunk_ids: list[str]) -> None:
        """Remember chunks that were retrieved so far."""
        self.seen_chunk_ids.update(chunk_ids)

    def format_for_llm(self) -> list[dict[str, str]]:
        """Return conversation history in OpenAI messages format.

        Used to provide context to the LLM for query rewriting and answer
        generation.
        """
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.messages
        ]

    def get_last_answer(self) -> str | None:
        """Return the content of the most recent assistant message."""
        for m in reversed(self.messages):
            if m["role"] == "assistant":
                return m["content"]
        return None

    def get_last_topic(self) -> str | None:
        """Return the most recent user query for context."""
        for m in reversed(self.messages):
            if m["role"] == "user":
                return m["content"]
        return None

    @property
    def turn_count(self) -> int:
        """Number of user-assistant exchanges."""
        return sum(1 for m in self.messages if m["role"] == "user")

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class SessionManager:
    """Thread-safe session registry with automatic expiry.

    Parameters
    ----------
    ttl_seconds:
        Sessions untouched for longer than this are evicted on access.
    max_sessions:
        Hard cap on concurrent sessions (LRU eviction when exceeded).
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        max_sessions: int = 10_000,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create(self, session_id: str | None = None) -> ChatSession:
        """Return an existing session or create a new one.

        If *session_id* is ``None`` a new random ID is generated.
        """
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.last_active = time.time()
            # Move to end (most-recently-used)
            self._sessions.move_to_end(session_id)
            return session

        sid = session_id or uuid.uuid4().hex[:12]
        session = ChatSession(session_id=sid)
        self._sessions[sid] = session
        self._sessions.move_to_end(sid)

        # Evict oldest if over capacity
        self._evict_if_needed()
        self._purge_expired()

        logger.debug("Session created: %s (total: %d)", sid, len(self._sessions))
        return session

    def get(self, session_id: str) -> ChatSession | None:
        """Return the session or ``None`` if expired / not found."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session.last_active > self._ttl:
            del self._sessions[session_id]
            return None
        session.last_active = time.time()
        self._sessions.move_to_end(session_id)
        return session

    def delete(self, session_id: str) -> bool:
        """Remove a session.  Returns ``True`` if it existed."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    @property
    def active_count(self) -> int:
        self._purge_expired()
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        while len(self._sessions) > self._max:
            self._sessions.popitem(last=False)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s.last_active > self._ttl
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.debug("Purged %d expired sessions", len(expired))
