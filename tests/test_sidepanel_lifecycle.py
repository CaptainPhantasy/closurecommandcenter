"""Tests for the Dual Console sidepanel endpoints.

Verifies:
  POST   /api/sidepanel/spawn         — creates an ephemeral agent and starts it
  DELETE /api/sidepanel/{agent_id}    — refuses non-sidepanel agents, tears down sidepanel agents
  index.html ships the Dual Console tab markup + sidepanel client code

The actual PTY plumbing is exercised end-to-end by tests/test_api_smoke.py;
here we focus on the sidepanel-specific endpoints + frontend wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _ensure_server_up():  # type: ignore[no-redef]
    yield


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """TestClient with isolated agents.json + a no-op spawn/kill so the test
    doesn't actually fork PTY processes."""
    agents_json = tmp_path / "agents.json"
    agents_json.write_text("{}")
    monkeypatch.setattr(server, "AGENTS_FILE", str(agents_json))
    # Stub PTY-related work so the test runs deterministically without forking
    monkeypatch.setattr(server, "spawn_process", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "kill_process", AsyncMock(return_value=None))
    return TestClient(server.app)


def test_spawn_returns_agent_id_and_name(client: TestClient) -> None:
    resp = client.post("/api/sidepanel/spawn")
    assert resp.status_code == 200
    j = resp.json()
    assert "agent_id" in j
    assert j["name"].startswith("sidepanel-")
    # 8 hex chars after the prefix
    assert len(j["name"]) == len("sidepanel-") + 8
    assert j["shell"]


def test_spawn_persists_agent_with_sidepanel_tags(client: TestClient) -> None:
    spawned = client.post("/api/sidepanel/spawn").json()
    listing = client.get("/api/agents").json()  # list[Agent]
    agents_by_id = {a["id"]: a for a in listing}
    assert spawned["agent_id"] in agents_by_id
    agent = agents_by_id[spawned["agent_id"]]
    assert "ephemeral" in agent["tags"]
    assert "sidepanel" in agent["tags"]
    assert agent["label"] == "Dual Console"
    assert agent["auto_start"] is True


def test_spawn_creates_unique_agents(client: TestClient) -> None:
    a = client.post("/api/sidepanel/spawn").json()
    b = client.post("/api/sidepanel/spawn").json()
    assert a["agent_id"] != b["agent_id"]


def test_delete_sidepanel_agent_succeeds(client: TestClient) -> None:
    spawned = client.post("/api/sidepanel/spawn").json()
    aid = spawned["agent_id"]
    resp = client.delete(f"/api/sidepanel/{aid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["agent_id"] == aid
    listing = client.get("/api/agents").json()
    assert all(a["id"] != aid for a in listing)


def test_delete_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.delete("/api/sidepanel/does-not-exist")
    assert resp.status_code == 404


def test_delete_refuses_non_sidepanel_agent(client: TestClient) -> None:
    """The DELETE endpoint must refuse agents that lack the 'sidepanel' tag —
    it's a privileged shortcut, not a generic delete."""
    # Create a regular agent via the existing /api/agents endpoint
    payload = {
        "name": "manual-agent",
        "directory": "/tmp",
        "command": "bash",
        "tags": ["manual"],
    }
    resp = client.post("/api/agents", json=payload)
    assert resp.status_code == 200
    aid = resp.json()["id"]
    bad = client.delete(f"/api/sidepanel/{aid}")
    assert bad.status_code == 400
    # Agent must still exist
    listing = client.get("/api/agents").json()
    assert any(a["id"] == aid for a in listing)


def test_index_html_has_dual_console_tab(client: TestClient) -> None:
    body = client.get("/").text
    assert ">Dual Console<" in body
    assert 'id="cb-page-dualterm"' in body
    assert 'id="dt-grid"' in body


def test_index_html_layout_toggle_buttons(client: TestClient) -> None:
    body = client.get("/").text
    for layout in ("single", "dual", "triple"):
        assert f'data-dt-layout="{layout}"' in body


def test_index_html_references_sidepanel_endpoints(client: TestClient) -> None:
    body = client.get("/").text
    assert "/api/sidepanel/spawn" in body
    assert "/api/sidepanel/" in body  # delete URL prefix
    assert "/ws/" in body  # WebSocket attach

