from fastapi.testclient import TestClient
from server import app


def test_aterm_status_returns_shared_session_state():
    client = TestClient(app)
    client.post("/api/do", json={"action": "create", "session": "status-test", "command": "/bin/bash"})
    resp = client.get("/api/aterm/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert "total" in data
    session = next(s for s in data["sessions"] if s["name"] == "status-test")
    assert {"id", "name", "status", "state", "errors", "tests", "hint", "actions"} <= set(session)
    client.post("/api/do", json={"action": "delete", "session": "status-test"})

