"""Tests for /api/system-health and /api/system-health/rescan (Step 12)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


# Bypass conftest's autouse server-required fixture
@pytest.fixture(scope="module", autouse=True)
def _ensure_server_up():  # type: ignore[no-redef]
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.app)


@pytest.fixture
def fake_scan_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Replace _get_system_scan_module with a stub so endpoint tests don't run real scans."""
    cache = tmp_path / ".floyd" / "system-health-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    seed = {
        "scanned_at": "2026-05-01T00:00:00+00:00",
        "duration_seconds": 1.5,
        "stats": {
            "total_apps_scanned": 7,
            "total_recoverable_gb": 2.4,
            "memory_total_gb": 24.0,
            "memory_used_gb": 18.0,
            "memory_free_gb": 6.0,
            "top_hog": "Google Chrome",
            "idle_app_count": 3,
        },
        "apps": [{"name": "Demo", "path": "/Applications/Demo.app", "size_mb": 1500.0,
                  "last_used_iso": None, "days_idle": 120, "recommendation": "remove", "notes": "idle 120d"}],
        "memory_hogs": [{"process_name": "Google Chrome", "pid_count": 31, "rss_gb": 3.5,
                         "pct_total": 18.5, "classification": "tame"}],
        "disk_recovery_candidates": [{"name": "Demo", "size_mb": 1500.0, "days_idle": 120, "path": "/Applications/Demo.app"}],
        "errors": [],
    }
    cache.write_text(json.dumps(seed))

    class _StubModule:
        @staticmethod
        def cache_is_fresh(seconds: int) -> bool:
            return True

        @staticmethod
        def read_cache():
            return json.loads(cache.read_text())

        @staticmethod
        def build_report():
            class _R:
                def to_json(self):
                    return json.dumps({**seed, "scanned_at": "2026-05-01T01:00:00+00:00", "duration_seconds": 0.1})
            return _R()

        @staticmethod
        def write_cache(report):
            cache.write_text(report.to_json())
            return cache

    # Reset cached module reference + inject stub
    monkeypatch.setattr(server, "_system_scan_module", _StubModule)
    return cache


def test_get_system_health_returns_cache(client: TestClient, fake_scan_module) -> None:
    resp = client.get("/api/system-health")
    assert resp.status_code == 200
    j = resp.json()
    assert j["stats"]["total_apps_scanned"] == 7
    assert j["cached"] is True
    assert j["fresh"] is True
    assert j["apps"][0]["name"] == "Demo"


def test_get_system_health_handles_missing_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the scanner stub reports no cache and no scan is possible, surface an error."""
    class _NoCache:
        @staticmethod
        def cache_is_fresh(seconds: int) -> bool: return False
        @staticmethod
        def read_cache(): return None
        @staticmethod
        def build_report():
            class _R:
                def to_json(self): return "{}"
            return _R()
        @staticmethod
        def write_cache(report): pass
    monkeypatch.setattr(server, "_system_scan_module", _NoCache)
    resp = client.get("/api/system-health")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_post_rescan_triggers_fresh_scan(client: TestClient, fake_scan_module) -> None:
    resp = client.post("/api/system-health/rescan")
    assert resp.status_code == 200
    j = resp.json()
    assert j["stats"]["total_apps_scanned"] == 7
    assert j["fresh"] is True
    assert "wall_clock_seconds" in j


def test_index_html_has_system_health_tab(client: TestClient) -> None:
    body = client.get("/").text
    assert ">System Health<" in body
    assert 'id="cb-page-health"' in body
    assert "/api/system-health" in body  # endpoint URL referenced in JS
    assert "health-rescan-btn" in body

