"""Tests for multi-turn chat (session manager + chat engine)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest


# ---------------------------------------------------------------------------
# ChatMessage / ChatResponse models
# ---------------------------------------------------------------------------


class TestChatModels:
    def test_chat_message_create(self):
        from pdf_parser.rag.models import ChatMessage

        msg = ChatMessage(role="user", content="你好")
        assert msg.role == "user"
        assert msg.content == "你好"
        assert msg.sources == []

    def test_chat_message_frozen(self):
        from pdf_parser.rag.models import ChatMessage

        msg = ChatMessage(role="assistant", content="answer")
        with pytest.raises(Exception):
            msg.content = "changed"

    def test_chat_response_create(self):
        from pdf_parser.rag.models import ChatResponse

        resp = ChatResponse(
            session_id="abc",
            answer="测试回答",
            turn_number=2,
        )
        assert resp.session_id == "abc"
        assert resp.answer == "测试回答"
        assert resp.turn_number == 2


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------


class TestChatSession:
    def test_create_session(self):
        from pdf_parser.rag.session import ChatSession

        s = ChatSession(session_id="test_session")
        assert s.session_id == "test_session"
        assert s.turn_count == 0
        assert s.messages == []

    def test_add_turns(self):
        from pdf_parser.rag.session import ChatSession

        s = ChatSession(session_id="s1")
        s.add_turn(role="user", content="问题1")
        s.add_turn(role="assistant", content="答案1")
        s.add_turn(role="user", content="问题2")

        assert s.turn_count == 2
        assert len(s.messages) == 3

    def test_format_for_llm(self):
        from pdf_parser.rag.session import ChatSession

        s = ChatSession(session_id="s1")
        s.add_turn(role="user", content="问题")
        s.add_turn(role="assistant", content="答案")

        formatted = s.format_for_llm()
        assert len(formatted) == 2
        assert formatted[0] == {"role": "user", "content": "问题"}
        assert formatted[1] == {"role": "assistant", "content": "答案"}

    def test_get_last_topic(self):
        from pdf_parser.rag.session import ChatSession

        s = ChatSession(session_id="s1")
        assert s.get_last_topic() is None

        s.add_turn(role="user", content="第一个问题")
        s.add_turn(role="assistant", content="第一个答案")
        s.add_turn(role="user", content="第二个问题")

        assert s.get_last_topic() == "第二个问题"

    def test_get_last_answer(self):
        from pdf_parser.rag.session import ChatSession

        s = ChatSession(session_id="s1")
        assert s.get_last_answer() is None

        s.add_turn(role="user", content="问题")
        s.add_turn(role="assistant", content="答案内容")

        assert s.get_last_answer() == "答案内容"

    def test_track_chunks(self):
        from pdf_parser.rag.session import ChatSession

        s = ChatSession(session_id="s1")
        s.track_chunks(["c001", "c002"])
        s.track_chunks(["c002", "c003"])

        assert s.seen_chunk_ids == {"c001", "c002", "c003"}

    def test_history_trimming(self):
        from pdf_parser.rag.session import ChatSession, MAX_HISTORY_TURNS

        s = ChatSession(session_id="s1")
        # Add more turns than MAX_HISTORY_TURNS
        for i in range(MAX_HISTORY_TURNS + 5):
            s.add_turn(role="user", content=f"问题{i}")
            s.add_turn(role="assistant", content=f"答案{i}")

        # History should be trimmed
        max_messages = MAX_HISTORY_TURNS * 2
        assert len(s.messages) <= max_messages + 2  # +2 for the last pair


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    @pytest.fixture
    def mgr(self):
        from pdf_parser.rag.session import SessionManager

        return SessionManager(ttl_seconds=3600)

    def test_create_new_session(self, mgr):
        s = mgr.get_or_create()
        assert s.session_id is not None
        assert len(s.session_id) == 12
        assert mgr.active_count >= 1

    def test_get_existing_session(self, mgr):
        s1 = mgr.get_or_create("my_session")
        s1.add_turn(role="user", content="test")

        s2 = mgr.get_or_create("my_session")
        assert s2.session_id == "my_session"
        assert s2.turn_count == 1
        assert s2.get_last_topic() == "test"

    def test_expired_session_returns_none(self, mgr):
        # Set TTL very short
        from pdf_parser.rag.session import SessionManager

        short_mgr = SessionManager(ttl_seconds=0)  # immediately expired
        s = short_mgr.get_or_create("expire_soon")
        s.add_turn(role="user", content="hi")

        # Should be expired now
        assert short_mgr.get("expire_soon") is None

    def test_delete_session(self, mgr):
        mgr.get_or_create("to_delete")
        assert mgr.delete("to_delete") is True
        assert mgr.delete("to_delete") is False
        assert mgr.get("to_delete") is None

    def test_active_count(self, mgr):
        for i in range(5):
            mgr.get_or_create(f"session_{i}")

        assert mgr.active_count == 5


# ---------------------------------------------------------------------------
# Chat API endpoint
# ---------------------------------------------------------------------------


class TestChatAPI:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from pdf_parser.api import app

        return TestClient(app)

    def test_chat_endpoint_exists(self, client):
        """The /rag/chat endpoint should be registered."""
        response = client.post(
            "/rag/chat",
            json={"message": "测试消息"},
        )
        # Will fail because LLMGateway isn't configured (no API key),
        # but the route should be registered (not 404)
        assert response.status_code != 404

    def test_chat_with_session_id(self, client):
        """Session ID should be accepted."""
        response = client.post(
            "/rag/chat",
            json={"message": "继续聊", "session_id": "test_session_123"},
        )
        assert response.status_code != 404


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
