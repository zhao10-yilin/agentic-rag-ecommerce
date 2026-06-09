"""Smoke tests for the service skeleton (API + tasks)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest
from fastapi.testclient import TestClient

from pdf_parser.api import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_parse_missing_file_and_path(client):
    response = client.post("/parse")
    assert response.status_code == 400
    assert "Either 'file'" in response.json()["detail"]


def test_parse_with_nonexistent_file_path(client):
    response = client.post("/parse?file_path=/does/not/exist.pdf")
    assert response.status_code == 404


def test_parse_upload_triggers_task_delay(monkeypatch, client, tmp_path):
    """Upload a dummy PDF and verify the endpoint returns a task_id."""
    captured = {}

    class FakeTask:
        id = "fake-task-id-123"

    def mock_delay(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeTask()

    monkeypatch.setattr("pdf_parser.api.parse_pdf_task.delay", mock_delay)

    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 fake pdf content")

    with dummy_pdf.open("rb") as fh:
        response = client.post(
            "/parse?enable_ocr=false&enable_cleaning=false",
            files={"file": ("dummy.pdf", fh, "application/pdf")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "fake-task-id-123"
    assert data["status"] == "submitted"
    assert data["file_id"] == "dummy"
    assert "/status/fake-task-id-123" in data["monitor_url"]

    # Verify parser config was forwarded correctly
    assert captured["kwargs"]["parser_config"]["enable_ocr"] is False
    assert captured["kwargs"]["cleaning_config"] is None


def test_status_endpoint_for_pending_task(monkeypatch, client):
    """Query a non-existent task — should report PENDING (Celery default)."""

    class FakeResult:
        status = "PENDING"
        ready = lambda self: False
        successful = lambda self: False

    monkeypatch.setattr("pdf_parser.api.AsyncResult", lambda task_id, app: FakeResult())

    response = client.get("/status/nonexistent-task-id")
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
