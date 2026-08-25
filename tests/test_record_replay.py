from fastapi.testclient import TestClient
from server import app


def test_record_captures_session_events():
    client = TestClient(app)
    session = "record-session-test"
    client.post("/api/do", json={"action": "delete", "session": session})
    try:
        created = client.post(
            "/api/do",
            json={"action": "create", "session": session, "command": "/bin/bash", "auto_start": True},
        ).json()
        assert created["ok"] is True

        started = client.post("/api/do", json={"action": "record", "session": session, "input": "start"}).json()
        assert started["ok"] is True
        recording_id = started["recording_id"]

        client.post("/api/do", json={"action": "run", "session": session, "input": "echo session-record-token"})
        stopped = client.post("/api/do", json={"action": "record", "session": session, "input": f"stop:{recording_id}"}).json()
        assert stopped["ok"] is True

        fetched = client.post("/api/do", json={"action": "record", "session": session, "input": f"get:{recording_id}"}).json()
        assert fetched["ok"] is True
        assert any(
            "session-record-token" in event.get("input", "") or "session-record-token" in event.get("output", "")
            for event in fetched["recording"]["events"]
        )
    finally:
        client.post("/api/do", json={"action": "delete", "session": session})

def test_replay_is_not_part_of_full_aterm_surface():
    client = TestClient(app)
    response = client.post("/api/do", json={"action": "replay", "session": "cache-test", "input": "pytest smoke"}).json()
    assert response["ok"] is False
    assert "invalid action" in response["error"]

