"""Fast HTTP-only smoke tests covering every advertised endpoint.

These run in under 5 seconds against a live server and are the front line of regression
detection. They do not exercise the UI; for that see `test_workflows_ui.py`.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.live_server

from _helpers import base_url, run_llm_do


def _client():
    return httpx.Client(base_url=base_url(), timeout=10.0)


def test_health_endpoint_returns_ok():
    with _client() as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_openapi_spec_advertises_critical_endpoints():
    with _client() as c:
        r = c.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    must_have = [
        "/api/health",
        "/api/agents",
        "/api/agents/{agent_id}",
        "/api/agents/{agent_id}/restart",
        "/api/agents/{agent_id}/status",
        "/api/llm/do",
        "/api/broadcast",
        "/api/export",
        "/api/import",
        "/api/automation/jobs",
    ]
    for p in must_have:
        assert p in paths, f"missing endpoint in OpenAPI: {p}"


def test_index_page_loads_and_references_xterm():
    with _client() as c:
        r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "<title>Floyd's Unified Command Kernel</title>" in body
    assert "xterm" in body.lower()
    assert "agent-list" in body  # sidebar mount point


def test_get_agents_returns_list_with_status_fields():
    with _client() as c:
        r = c.get("/api/agents")
    assert r.status_code == 200
    agents = r.json()
    assert isinstance(agents, list)
    for a in agents:
        # Every agent must carry these fields after the GET enrichment
        for key in (
            "id",
            "name",
            "directory",
            "command",
            "status",
            "uptime",
            "restart_count",
        ):
            assert key in a, f"agent missing field {key}: {a}"
        assert a["status"] in ("running", "stopped", "exited")


def test_performance_endpoint_returns_metrics():
    with _client() as c:
        r = c.get("/api/performance")
    assert r.status_code == 200
    body = r.json()
    assert "running_agents" in body
    assert "max_rss_kb" in body or "error" in body


def test_tags_endpoint_lists_unique_tags():
    with _client() as c:
        r = c.get("/api/tags")
    assert r.status_code == 200
    tags = r.json()
    assert isinstance(tags, list)
    assert tags == sorted(tags)  # API contract: alphabetized
    assert len(tags) == len(set(tags))  # API contract: unique


def test_export_returns_json_attachment():
    with _client() as c:
        r = c.get("/api/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers.get("content-disposition", "")
    data = r.json()
    assert isinstance(data, list)


def test_cron_helper_exposes_presets():
    with _client() as c:
        r = c.get("/api/cron/helper")
    assert r.status_code == 200
    presets = r.json()
    labels = [p["label"] for p in presets]
    assert "Every minute" in labels
    assert "Daily at midnight" in labels


def test_automation_jobs_endpoint_returns_list():
    with _client() as c:
        r = c.get("/api/automation/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_invalid_agent_rejected_with_helpful_error():
    with _client() as c:
        r = c.post(
            "/api/agents",
            json={
                "name": "bad",
                "directory": "/this/path/definitely/does/not/exist/xyz",
                "command": "bash",
            },
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("directory does not exist" in str(d).lower() for d in detail), detail


def test_create_invalid_command_rejected():
    with _client() as c:
        r = c.post(
            "/api/agents",
            json={
                "name": "bad-cmd",
                "directory": "/tmp",
                "command": 'unterminated "quote',
            },
        )
    assert r.status_code == 422


def test_invalid_name_empty_rejected():
    with _client() as c:
        r = c.post(
            "/api/agents",
            json={"name": "   ", "directory": "/tmp", "command": "bash"},
        )
    assert r.status_code == 422
    assert "must not be empty" in str(r.json()["detail"]).lower()


def test_invalid_name_too_long_rejected():
    with _client() as c:
        r = c.post(
            "/api/agents",
            json={"name": "x" * 200, "directory": "/tmp", "command": "bash"},
        )
    assert r.status_code == 422
    assert "100 characters" in str(r.json()["detail"]).lower()


def test_invalid_name_with_slash_rejected():
    """Names appear in toasts and as suggested download filenames — block path
    separators so the UX stays predictable."""
    with _client() as c:
        r = c.post(
            "/api/agents",
            json={"name": "../etc/passwd", "directory": "/tmp", "command": "bash"},
        )
    assert r.status_code == 422
    detail = str(r.json()["detail"]).lower()
    assert "/" in detail or "name" in detail


def test_invalid_cron_expression_rejected():
    with _client() as c:
        r = c.post(
            "/api/agents",
            json={
                "name": "bad-cron",
                "directory": "/tmp",
                "command": "echo hi",
                "cron_expression": "not a cron",
            },
        )
    assert r.status_code == 422


def test_agent_crud_lifecycle_via_api():
    """Workflow #2 — Add / Update / Delete an agent at the HTTP layer."""
    name = f"smoke-test-{uuid.uuid4().hex[:8]}"
    with _client() as c:
        # Create
        created = c.post(
            "/api/agents",
            json={
                "name": name,
                "directory": "/tmp",
                "command": "bash",
                "tags": ["smoke", "ephemeral"],
            },
        )
        assert created.status_code == 200, created.text
        agent_id = created.json()["id"]
        try:
            # Update
            updated = c.put(
                f"/api/agents/{agent_id}",
                json={"order": 999, "pinned": False},
            )
            assert updated.status_code == 200
            assert updated.json()["order"] == 999

            # Listing includes it
            listing = c.get("/api/agents").json()
            assert any(a["id"] == agent_id for a in listing)
        finally:
            # Delete (always cleanup)
            deleted = c.delete(f"/api/agents/{agent_id}")
            assert deleted.status_code == 200
            assert deleted.json() == {"ok": True}

        # Confirm gone
        listing_after = c.get("/api/agents").json()
        assert not any(a["id"] == agent_id for a in listing_after)


def test_llm_list_returns_known_floyd_agents():
    """Workflow #1 prerequisite — `list` action surfaces all configured agents."""
    resp = run_llm_do("list")
    assert resp["ok"] is True
    names = {a["name"] for a in (resp.get("agents") or [])}
    # Must include at least one floyd agent
    assert any(n.startswith("FLOYD-") for n in names), (
        f"no floyd agents listed: {names}"
    )


def test_llm_unknown_agent_returns_helpful_404():
    with _client() as c:
        r = c.post(
            "/api/llm/do",
            json={"action": "read", "agent": "definitely-not-a-real-agent-xyz123"},
        )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not found" in detail["error_message"].lower()
    assert "list" in detail["actions_available"]


def test_llm_run_action_requires_input():
    with _client() as c:
        r = c.post(
            "/api/llm/do",
            json={"action": "run", "agent": "FLOYD-STABILITY"},
        )
    # Server returns 200 with an error envelope, per the LLM-first design
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "input" in body["error_message"].lower()

