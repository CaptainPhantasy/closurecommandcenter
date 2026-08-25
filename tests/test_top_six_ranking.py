"""Tests for /api/projects/top-six-active and the ranker function (Step 5).

Verifies:
- Pure ranker correctness (sort, tiebreak, completion-100 filtering, top-6 cap)
- Endpoint serves the same shape as expected by the Workspace UI
- Workspace tab markup is shipped
- Sidepanel spawn endpoint accepts the optional directory/label/extra_tags body
  used by the Workspace renderer
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
    agents_json = tmp_path / "agents.json"
    agents_json.write_text("{}")
    monkeypatch.setattr(server, "AGENTS_FILE", str(agents_json))
    monkeypatch.setattr(server, "spawn_process", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "kill_process", AsyncMock(return_value=None))
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# Pure ranker
# ---------------------------------------------------------------------------


def _proj(name: str, pct: int, last_bootstrap: str = "", path: str = "") -> dict:
    return {
        "name": name,
        "path": path or f"/tmp/{name}",
        "completion_percentage": pct,
        "last_bootstrap": last_bootstrap,
    }


def test_rank_filters_completed_projects() -> None:
    projects = [
        _proj("done-a", 100, "2026-04-01"),
        _proj("active-b", 80, "2026-04-15"),
    ]
    ranked = server._rank_top_six(projects)
    names = [p["name"] for p in ranked]
    assert "done-a" not in names
    assert "active-b" in names


def test_rank_sorts_by_completion_desc() -> None:
    projects = [
        _proj("low", 20),
        _proj("mid", 60),
        _proj("high", 90),
    ]
    ranked = server._rank_top_six(projects)
    assert [p["name"] for p in ranked] == ["high", "mid", "low"]


def test_rank_tiebreak_oldest_bootstrap_wins() -> None:
    """At equal completion%, the project with the older last_bootstrap is ranked higher
    (it's been waiting longer and benefits more from re-bootstrapping)."""
    projects = [
        _proj("recent", 50, "2026-04-25"),
        _proj("old", 50, "2026-01-01"),
        _proj("never", 50, ""),  # empty string sorts before any ISO date
    ]
    ranked = server._rank_top_six(projects)
    # never (empty) < old (Jan) < recent (Apr) — ascending sort means never is first
    assert [p["name"] for p in ranked] == ["never", "old", "recent"]


def test_rank_tops_at_six_even_with_more_input() -> None:
    projects = [_proj(f"p{i}", 50 + i) for i in range(15)]
    ranked = server._rank_top_six(projects)
    assert len(ranked) == 6
    # Top 6 are the highest completion%
    assert [p["name"] for p in ranked][0] == "p14"


def test_rank_handles_empty_input() -> None:
    assert server._rank_top_six([]) == []


def test_rank_treats_missing_completion_as_zero() -> None:
    projects = [{"name": "no-pct", "path": "/x"}]
    ranked = server._rank_top_six(projects)
    assert ranked == [{"name": "no-pct", "path": "/x"}]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


def test_endpoint_returns_canonical_version_and_projects(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _time
    monkeypatch.setattr(server, "_PROJECTS_CACHE", {"ts": _time.time(), "data": [
        _proj("alpha", 90, "2026-04-25"),
        _proj("beta", 80, "2026-04-20"),
        _proj("done", 100, ""),
    ]})
    monkeypatch.setattr(server, "_CANONICAL_VERSION", "1.6.0")
    resp = client.get("/api/projects/top-six-active")
    assert resp.status_code == 200
    j = resp.json()
    assert j["canonical_version"] == "1.6.0"
    names = [p["name"] for p in j["projects"]]
    assert names == ["alpha", "beta"]  # done filtered out


def test_endpoint_caps_at_six(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time
    big = [_proj(f"p{i}", 70 + (i % 25), f"2026-04-{(i%28)+1:02d}") for i in range(20)]
    monkeypatch.setattr(server, "_PROJECTS_CACHE", {"ts": _time.time(), "data": big})
    resp = client.get("/api/projects/top-six-active")
    j = resp.json()
    assert len(j["projects"]) == 6


# ---------------------------------------------------------------------------
# Sidepanel spawn body extension (Step 5 reuses the spawn endpoint with a body)
# ---------------------------------------------------------------------------


def test_sidepanel_spawn_accepts_directory_and_extra_tags(
    client: TestClient, tmp_path: Path
) -> None:
    proj_dir = tmp_path / "demo-project"
    proj_dir.mkdir()
    body = {
        "directory": str(proj_dir),
        "label": "Workspace · demo",
        "name_prefix": "ws-demo",
        "extra_tags": ["workspace"],
    }
    resp = client.post("/api/sidepanel/spawn", json=body)
    assert resp.status_code == 200
    j = resp.json()
    assert j["directory"] == str(proj_dir)
    assert j["name"].startswith("ws-demo-")
    listing = client.get("/api/agents").json()
    agent = next(a for a in listing if a["id"] == j["agent_id"])
    assert agent["directory"] == str(proj_dir)
    assert agent["label"] == "Workspace · demo"
    assert "workspace" in agent["tags"]
    assert "sidepanel" in agent["tags"]   # base tags always present


def test_sidepanel_spawn_falls_back_when_directory_missing(client: TestClient) -> None:
    """Bad directory falls back to $HOME silently — Workspace shouldn't fail
    catastrophically just because a project was moved."""
    body = {"directory": "/this/path/does/not/exist"}
    resp = client.post("/api/sidepanel/spawn", json=body)
    assert resp.status_code == 200
    j = resp.json()
    # Directory must be a real dir
    assert Path(j["directory"]).is_dir()


# ---------------------------------------------------------------------------
# UI markup
# ---------------------------------------------------------------------------


def test_workspace_tab_replaces_placeholder(client: TestClient) -> None:
    body = client.get("/").text
    assert ">Workspace<" in body
    # The old placeholder copy should be gone
    assert "6-project workspace lands in Step 5" not in body
    # Workspace markup present
    assert 'id="ws-grid"' in body
    assert 'id="ws-refresh-btn"' in body


def test_index_html_references_top_six_endpoint(client: TestClient) -> None:
    body = client.get("/").text
    assert "/api/projects/top-six-active" in body

