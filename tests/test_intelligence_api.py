"""Tests for the Intelligence layer (/api/intel/*).

Covers session CRUD, messages, models catalog, stats, hooks, checkpoints,
and the chat/stream endpoint with a mocked Z.ai upstream (no real API calls).
"""

import pytest

from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session(client):
    resp = client.post("/api/intel/session", json={"title": "test", "model": "glm-5.1"})
    assert resp.status_code == 200
    return resp.json()["session_id"]


# -- Models ------------------------------------------------------------------

class TestIntelModels:
    def test_models_returns_list(self, client):
        resp = client.get("/api/intel/models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_model_entry_has_required_fields(self, client):
        data = client.get("/api/intel/models").json()
        sample = data[0]
        assert "id" in sample


# -- Sessions ----------------------------------------------------------------

class TestIntelSessions:
    def test_create_session(self, client):
        resp = client.post(
            "/api/intel/session",
            json={"title": "my session", "model": "glm-5.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"]
        assert body["title"] == "my session"
        assert body["model"] == "glm-5.1"

    def test_create_session_defaults(self, client):
        resp = client.post("/api/intel/session", json={})
        assert resp.status_code == 200
        assert resp.json()["model"] == "glm-5.1"

    def test_get_session(self, client, session):
        resp = client.get(f"/api/intel/session/{session}")
        assert resp.status_code == 200
        assert resp.json()["id"] == session

    def test_get_session_not_found(self, client):
        resp = client.get("/api/intel/session/nonexistent-sid")
        assert resp.status_code == 404

    def test_delete_session(self, client, session):
        resp = client.delete(f"/api/intel/session/{session}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert client.get(f"/api/intel/session/{session}").status_code == 404

    def test_delete_session_not_found(self, client):
        assert client.delete("/api/intel/session/nope").status_code == 404

    def test_get_messages_empty(self, client, session):
        resp = client.get(f"/api/intel/session/{session}/messages")
        assert resp.status_code == 200
        assert resp.json() == []


# -- Stats -------------------------------------------------------------------

class TestIntelStats:
    def test_stats_returns_store_and_routing(self, client):
        resp = client.get("/api/intel/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "store" in data
        assert "routing" in data


# -- Hooks -------------------------------------------------------------------

class TestIntelHooks:
    def test_hooks_list(self, client):
        resp = client.get("/api/intel/hooks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# -- Checkpoints -------------------------------------------------------------

class TestIntelCheckpoints:
    def test_checkpoint_create_and_list(self, client, session):
        create = client.post(
            "/api/intel/checkpoint",
            json={"session_id": session, "label": "manual"},
        )
        assert create.status_code == 200
        cp = create.json()
        assert cp["id"]

        listed = client.get(f"/api/intel/session/{session}/checkpoints")
        assert listed.status_code == 200
        ids = [c["id"] for c in listed.json()]
        assert cp["id"] in ids

    def test_checkpoint_missing_session(self, client):
        resp = client.post("/api/intel/checkpoint", json={"label": "x"})
        assert resp.status_code == 400


# -- Chat stream (mocked upstream) -------------------------------------------

class TestIntelChatStream:
    def test_stream_requires_session_id(self, client):
        resp = client.post(
            "/api/intel/chat/stream",
            json={"message": "hello"},
        )
        assert resp.status_code == 400

    def test_stream_requires_message(self, client, session):
        resp = client.post(
            "/api/intel/chat/stream",
            json={"session_id": session},
        )
        assert resp.status_code == 400

    def test_stream_missing_credentials_errors(self, client, session, monkeypatch):
        """Without a Z.ai key in the vault the stream must not complete."""
        from modules.intelligence import routes as intel_routes

        monkeypatch.setattr(intel_routes, "_read_vault", lambda: {})
        resp = client.post(
            "/api/intel/chat/stream",
            json={"session_id": session, "message": "hello"},
        )
        assert resp.status_code in (400, 500)


# -- Tasks -------------------------------------------------------------------

class TestIntelTasks:
    def test_tasks_list(self, client):
        resp = client.get("/api/intel/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_task_not_found(self, client):
        resp = client.get("/api/intel/task/no-such-task")
        assert resp.status_code == 404

    def test_cancel_not_found(self, client):
        resp = client.post("/api/intel/task/no-such-task/cancel")
        assert resp.status_code in (400, 404)


# -- Tool history ------------------------------------------------------------

class TestIntelToolHistory:
    def test_tool_history_empty(self, client, session):
        resp = client.get(f"/api/intel/session/{session}/tools")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

