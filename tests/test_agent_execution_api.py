"""Tests for Agent Execution backend API (BETA-05 functional)."""

import pytest

import json

from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client():
    return TestClient(app)


class TestDoEndpoint:
    def test_list_empty(self, client):
        resp = client.post("/api/do", json={"action": "list"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert isinstance(data["sessions"], list)
        assert "hint" in data
        assert "actions" in data

    def test_invalid_action(self, client):
        resp = client.post("/api/do", json={"action": "bogus"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "invalid action" in data["error"]

    def test_create_session(self, client):
        resp = client.post(
            "/api/do",
            json={
                "action": "create",
                "session": "unit-test",
                "command": "/bin/bash",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "id" in data
        assert data["status"] == "stopped"
        # Cleanup
        client.post("/api/do", json={"action": "delete", "session": "unit-test"})

    def test_create_session_returns_structured_success_not_500(self, client):
        resp = client.post(
            "/api/do",
            json={"action": "create", "session": "create-regression", "command": "/bin/bash"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "stopped"
        assert data["id"]
        assert {"start", "delete", "note", "history", "checkpoint", "record", "automate"} <= set(data["actions"])
        client.post("/api/do", json={"action": "delete", "session": "create-regression"})

    def test_create_session_auto_start_starts_real_pty(self, client):
        resp = client.post(
            "/api/do",
            json={
                "action": "create",
                "session": "autostart-regression",
                "command": "/bin/bash",
                "auto_start": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "ready"
        assert "pid" in data
        client.post("/api/do", json={"action": "delete", "session": "autostart-regression"})

    def test_session_lifecycle(self, client):
        """create -> start -> run -> read -> stop -> delete"""
        # Create
        resp = client.post(
            "/api/do",
            json={
                "action": "create",
                "session": "lifecycle-test",
                "command": "/bin/bash",
            },
        )
        assert resp.json()["ok"] is True

        # Start
        resp = client.post(
            "/api/do", json={"action": "start", "session": "lifecycle-test"}
        )
        assert resp.json()["ok"] is True
        assert resp.json()["status"] == "ready"

        # Run
        resp = client.post(
            "/api/do",
            json={"action": "run", "session": "lifecycle-test", "input": "echo hello"},
        )
        assert resp.json()["ok"] is True
        assert "$ echo hello" in resp.json()["output"]

        # Read
        resp = client.post(
            "/api/do", json={"action": "read", "session": "lifecycle-test"}
        )
        assert resp.json()["ok"] is True
        assert resp.json()["status"] == "ready"

        # Stop
        resp = client.post(
            "/api/do", json={"action": "stop", "session": "lifecycle-test"}
        )
        assert resp.json()["ok"] is True
        assert resp.json()["status"] == "stopped"

        # Delete
        resp = client.post(
            "/api/do", json={"action": "delete", "session": "lifecycle-test"}
        )
        assert resp.json()["ok"] is True

        # Verify gone
        resp = client.post("/api/do", json={"action": "list"})
        names = [s["name"] for s in resp.json()["sessions"]]
        assert "lifecycle-test" not in names

    def test_session_not_found(self, client):
        resp = client.post("/api/do", json={"action": "read", "session": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "not found" in resp.json()["error"]

    def test_run_without_input(self, client):
        resp = client.post(
            "/api/do",
            json={"action": "create", "session": "run-test", "command": "/bin/bash"},
        )
        resp = client.post("/api/do", json={"action": "run", "session": "run-test"})
        assert resp.json()["ok"] is False
        assert "input required" in resp.json()["error"]
        client.post("/api/do", json={"action": "delete", "session": "run-test"})

    def test_cancel_session(self, client):
        client.post(
            "/api/do",
            json={"action": "create", "session": "cancel-test", "command": "/bin/bash"},
        )
        client.post("/api/do", json={"action": "start", "session": "cancel-test"})
        resp = client.post(
            "/api/do", json={"action": "cancel", "session": "cancel-test"}
        )
        assert resp.json()["ok"] is True
        client.post("/api/do", json={"action": "delete", "session": "cancel-test"})

    def test_bridge_action(self, client):
        resp = client.post("/api/do", json={"action": "bridge"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "bridge_status" in resp.json()

    def test_note_action(self, client):
        client.post(
            "/api/do",
            json={"action": "create", "session": "note-test", "command": "/bin/bash"},
        )
        # Set note
        resp = client.post(
            "/api/do",
            json={"action": "note", "session": "note-test", "input": "test note"},
        )
        assert resp.json()["ok"] is True
        # Read note
        resp = client.post("/api/do", json={"action": "note", "session": "note-test"})
        assert resp.json()["scratchpad"] == "test note"
        client.post("/api/do", json={"action": "delete", "session": "note-test"})

    def test_search_action(self, client):
        resp = client.post("/api/do", json={"action": "search", "input": "test"})
        assert resp.json()["ok"] is True

    def test_history_action(self, client):
        client.post(
            "/api/do",
            json={"action": "create", "session": "hist-test", "command": "/bin/bash"},
        )
        resp = client.post(
            "/api/do", json={"action": "history", "session": "hist-test"}
        )
        assert resp.json()["ok"] is True
        client.post("/api/do", json={"action": "delete", "session": "hist-test"})

    def test_broadcast_action(self, client):
        resp = client.post("/api/do", json={"action": "broadcast", "input": "test"})
        assert resp.json()["ok"] is True

    def test_create_without_session_name(self, client):
        resp = client.post("/api/do", json={"action": "create", "command": "/bin/bash"})
        assert resp.json()["ok"] is False
        assert "session" in resp.json()["error"].lower()

    def test_full_aterm_history_search_and_broadcast(self, client):
        for name in ("full-a", "full-b"):
            client.post("/api/do", json={"action": "delete", "session": name})
        try:
            for name in ("full-a", "full-b"):
                resp = client.post(
                    "/api/do",
                    json={"action": "create", "session": name, "command": "/bin/bash", "auto_start": True},
                )
                assert resp.json()["ok"] is True

            run = client.post("/api/do", json={"action": "run", "session": "full-a", "input": "echo needle-aterm-full"})
            assert run.json()["ok"] is True

            history = client.post("/api/do", json={"action": "history", "session": "full-a", "lines": 5}).json()
            assert history["ok"] is True
            assert any("echo needle-aterm-full" in item for item in history["history"])

            search = client.post("/api/do", json={"action": "search", "input": "needle-aterm-full"}).json()
            assert search["ok"] is True
            assert search["results"]
            assert search["results"][0]["session"] == "full-a"
            assert any("needle-aterm-full" in match for match in search["results"][0]["matches"])

            broadcast = client.post("/api/do", json={"action": "broadcast", "input": "echo broadcast-aterm-token"}).json()
            assert broadcast["ok"] is True
            assert broadcast["sent"] == 2
            assert broadcast["total"] == 2

            for name in ("full-a", "full-b"):
                read = client.post("/api/do", json={"action": "read", "session": name}).json()
                assert "broadcast-aterm-token" in read["output"]
        finally:
            for name in ("full-a", "full-b"):
                client.post("/api/do", json={"action": "delete", "session": name})


    def test_full_aterm_checkpoint_record_verify_batch_and_automate(self, client):
        session = "full-control"
        client.post("/api/do", json={"action": "delete", "session": session})
        try:
            created = client.post(
                "/api/do",
                json={"action": "create", "session": session, "command": "/bin/bash", "auto_start": True},
            ).json()
            assert created["ok"] is True

            client.post("/api/do", json={"action": "note", "session": session, "input": "before checkpoint"})
            client.post("/api/do", json={"action": "run", "session": session, "input": "echo checkpoint-before"})
            checkpoint = client.post("/api/do", json={"action": "checkpoint", "session": session, "input": "save:main"}).json()
            assert checkpoint["ok"] is True
            checkpoint_id = checkpoint["checkpoint_id"]

            client.post("/api/do", json={"action": "note", "session": session, "input": "after checkpoint"})
            client.post("/api/do", json={"action": "run", "session": session, "input": "echo checkpoint-after"})
            listed = client.post("/api/do", json={"action": "checkpoint", "session": session, "input": "list"}).json()
            assert any(item["id"] == checkpoint_id for item in listed["checkpoints"])
            restored = client.post("/api/do", json={"action": "checkpoint", "session": session, "input": f"restore:{checkpoint_id}"}).json()
            assert restored["ok"] is True
            assert client.post("/api/do", json={"action": "note", "session": session}).json()["scratchpad"] == "before checkpoint"
            read = client.post("/api/do", json={"action": "read", "session": session}).json()
            assert "checkpoint-before" in read["output"]
            assert "checkpoint-after" not in read["output"]

            recording = client.post("/api/do", json={"action": "record", "session": session, "input": "start"}).json()
            assert recording["ok"] is True
            recording_id = recording["recording_id"]
            client.post("/api/do", json={"action": "run", "session": session, "input": "echo recording-token"})
            stopped = client.post("/api/do", json={"action": "record", "session": session, "input": f"stop:{recording_id}"}).json()
            assert stopped["ok"] is True
            got = client.post("/api/do", json={"action": "record", "session": session, "input": f"get:{recording_id}"}).json()
            assert any("recording-token" in event.get("output", "") or "recording-token" in event.get("input", "") for event in got["recording"]["events"])

            verified = client.post("/api/do", json={"action": "verify", "session": session, "input": "echo verify-aterm-token"}).json()
            assert verified["ok"] is True
            assert verified["passed"] is True
            assert "verify-aterm-token" in verified["output"]

            batch = client.post(
                "/api/do",
                json={
                    "action": "batch",
                    "session": session,
                    "input": json.dumps([
                        {"action": "run", "input": "echo batch-token"},
                        {"action": "read"},
                    ]),
                },
            ).json()
            assert batch["ok"] is True
            assert batch["count"] == 2
            assert "batch-token" in batch["results"][1]["output"]

            automation = client.post(
                "/api/do",
                json={"action": "automate", "session": session, "input": "register", "cron_expression": "*/5 * * * *"},
            ).json()
            assert automation["ok"] is True
            job_id = automation["job_id"]
            jobs = client.post("/api/do", json={"action": "automate", "session": session, "input": "list"}).json()
            assert any(job["id"] == job_id for job in jobs["jobs"])
            cancelled = client.post("/api/do", json={"action": "automate", "session": session, "input": f"cancel:{job_id}"}).json()
            assert cancelled["ok"] is True
        finally:
            client.post("/api/do", json={"action": "delete", "session": session})

    def test_aterm_events_subscribe_filters_session_events(self, client):
        session = "events-filter"
        client.post("/api/do", json={"action": "delete", "session": session})
        try:
            with client.websocket_connect("/ws/events") as websocket:
                assert websocket.receive_json()["type"] == "sessions_list"
                websocket.send_json({"type": "subscribe", "sessions": [session], "types": ["session_created"]})
                assert websocket.receive_json()["type"] == "subscribed"

                created = client.post("/api/do", json={"action": "create", "session": session, "command": "/bin/bash"}).json()
                assert created["ok"] is True

                event = websocket.receive_json()
                assert event["type"] == "session_created"
                assert event["session"]["name"] == session
        finally:
            client.post("/api/do", json={"action": "delete", "session": session})
