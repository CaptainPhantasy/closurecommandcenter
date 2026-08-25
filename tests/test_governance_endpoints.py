"""Tests for Kernel Project Control endpoints.

Authority: SSOT/control-center_SSOT.md § Project Control
Endpoints under test:
  GET /api/projects              — project list with status + report
  GET /api/quarantine-summary    — aggregate quarantine state

These tests use TestClient against the FastAPI app directly, monkeypatching
server._DRIVES to point at synthetic projects in tmp_path. They do NOT require
a live TCC server, so we override the conftest-level autouse fixture that
skips tests when the server is offline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make server.py importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


# Override the conftest autouse server-required fixture for this file —
# these tests run against the in-process FastAPI app, no live server needed.
@pytest.fixture(scope="module", autouse=True)
def _ensure_server_up():  # type: ignore[no-redef]
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.app)


def _make_project(
    drive: Path,
    name: str,
    *,
    floyd_md: bool = True,
    report: dict | None = None,
    stamp_version: str | None = None,
    quarantine_files: list[tuple[str, str]] | None = None,
) -> Path:
    """Build a synthetic project on a synthetic drive.

    quarantine_files: list of (date_subdir, relative_filename) tuples.
    """
    proj = drive / name
    proj.mkdir(parents=True, exist_ok=True)
    if floyd_md:
        (proj / "FLOYD.md").write_text(f"# {name} — FLOYD.md\n**Version:** 1.0.0\n")
    if report is not None:
        ssot = proj / "SSOT"
        ssot.mkdir(parents=True, exist_ok=True)
        (ssot / "repository_report.json").write_text(json.dumps(report))
    if stamp_version is not None:
        floyd_dir = proj / ".floyd"
        floyd_dir.mkdir(parents=True, exist_ok=True)
        (floyd_dir / ".supercache_version").write_text(stamp_version)
    if quarantine_files:
        qroot = proj / ".floyd" / "quarantine"
        for date_sub, rel in quarantine_files:
            tgt = qroot / date_sub / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_text("quarantined content")
    return proj


@pytest.fixture
def synthetic_drives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Two synthetic drives with multiple projects across statuses."""
    drive_a = tmp_path / "DriveA"
    drive_b = tmp_path / "DriveB"
    drive_a.mkdir()
    drive_b.mkdir()

    # GOVERNED: report exists with recent _last_verified
    from datetime import datetime, timezone, timedelta

    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _make_project(
        drive_a,
        "alpha-governed",
        report={
            "project_name": "alpha-governed",
            "completion_percentage": 86,
            "tech_stack": ["Python", "FastAPI"],
            "industry_vertical": "DevTools",
            "business_model": "B2B",
            "team_size_minimum": 8,
            "complexity_score": 7,
            "go_to_market_timeline": "Q3",
            "scalability_needs": "moderate",
            "target_users": ["devs"],
            "key_features": ["x", "y"],
            "risks": ["tech debt"],
            "technical_debt": "low",
            "gate_statuses": {
                "build_run": "PASS",
                "primary_journey": "PASS",
                "automated_tests": "PASS",
                "e2e_tests": "PASS",
                "multi_min_human_sim": "UNKNOWN",
                "security": "PASS",
                "demo": "PASS",
            },
            "_evidence": {},
            "_critic_notes": [],
            "_verified": True,
            "_critic_rounds": 3,
            "_last_verified": recent,
            "_verified_by": "test",
        },
        stamp_version="1.6.0",
    )

    # CANDIDATE: FLOYD.md but no report
    _make_project(drive_a, "bravo-candidate")

    # CANDIDATE: report exists but _last_verified is stale (>7 days)
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _make_project(
        drive_b,
        "charlie-stale",
        report={
            "project_name": "charlie-stale",
            "completion_percentage": 14,
            "tech_stack": ["Go"],
            "industry_vertical": "Unknown",
            "business_model": "Unknown",
            "team_size_minimum": 4,
            "complexity_score": 3,
            "go_to_market_timeline": "TBD",
            "scalability_needs": "low",
            "target_users": [],
            "key_features": [],
            "risks": [],
            "technical_debt": "unknown",
            "gate_statuses": {
                g: "UNKNOWN"
                for g in [
                    "build_run",
                    "primary_journey",
                    "automated_tests",
                    "e2e_tests",
                    "multi_min_human_sim",
                    "security",
                    "demo",
                ]
            },
            "_evidence": {},
            "_critic_notes": [],
            "_verified": False,
            "_critic_rounds": 1,
            "_last_verified": stale,
            "_verified_by": "test",
        },
    )

    # DRIFTED: stamp version doesn't match canonical
    _make_project(
        drive_b,
        "delta-drifted",
        stamp_version="1.4.0",  # canonical patched below to "1.6.0"
    )

    # Project with a quarantine entry (counts toward quarantine summary only)
    _make_project(
        drive_b,
        "echo-quarantined",
        quarantine_files=[
            ("2026-04-30", "removed-file.txt"),
            (
                "2026-04-30",
                "WHY.md",
            ),  # not counted in scan logic (no .WHY.md suffix here)
        ],
    )

    monkeypatch.setattr(server, "_DRIVES", (drive_a, drive_b))
    monkeypatch.setattr(server, "_CANONICAL_VERSION", "1.6.0")
    monkeypatch.setattr(server, "_PROJECTS_CACHE", {"ts": 0.0, "data": None})
    monkeypatch.setattr(server, "_QUARANTINE_CACHE", {"ts": 0.0, "data": None})
    return drive_a, drive_b


# ---------------------------------------------------------------------------
# /api/projects
# ---------------------------------------------------------------------------


def test_projects_endpoint_returns_canonical_version(
    client: TestClient, synthetic_drives
) -> None:
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    j = resp.json()
    assert j["canonical_version"] == "1.6.0"
    assert "projects" in j
    assert j["cached"] is False


def test_projects_endpoint_lists_all_synthetic_projects(
    client: TestClient, synthetic_drives
) -> None:
    j = client.get("/api/projects").json()
    names = {p["name"] for p in j["projects"]}
    assert names == {
        "alpha-governed",
        "bravo-candidate",
        "charlie-stale",
        "delta-drifted",
        "echo-quarantined",
    }


def test_projects_endpoint_status_classification(
    client: TestClient, synthetic_drives
) -> None:
    j = client.get("/api/projects").json()
    by_name = {p["name"]: p for p in j["projects"]}
    assert by_name["alpha-governed"]["status"] == "GOVERNED"
    assert by_name["bravo-candidate"]["status"] == "CANDIDATE"
    assert by_name["charlie-stale"]["status"] == "CANDIDATE"  # stale >7d
    assert by_name["delta-drifted"]["status"] == "DRIFTED"


def test_projects_endpoint_sort_completion_desc(
    client: TestClient, synthetic_drives
) -> None:
    j = client.get("/api/projects").json()
    pcts = [p["completion_percentage"] for p in j["projects"]]
    # Top should be alpha-governed (86), tail includes 0% projects
    assert pcts[0] == 86
    assert pcts == sorted(pcts, reverse=True)


def test_projects_endpoint_includes_links(client: TestClient, synthetic_drives) -> None:
    j = client.get("/api/projects").json()
    by_name = {p["name"]: p for p in j["projects"]}
    alpha = by_name["alpha-governed"]
    assert alpha["links"]["floyd_md"].startswith("/api/fs/serve?path=")
    assert alpha["links"]["floyd_md"].endswith("/FLOYD.md")
    assert alpha["links"]["report_json"] is not None
    bravo = by_name["bravo-candidate"]
    assert bravo["links"]["report_json"] is None  # no report file


def test_projects_endpoint_cache_serves_second_call(
    client: TestClient, synthetic_drives
) -> None:
    first = client.get("/api/projects").json()
    second = client.get("/api/projects").json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(first["projects"]) == len(second["projects"])


# ---------------------------------------------------------------------------
# /api/quarantine-summary
# ---------------------------------------------------------------------------


def test_quarantine_summary_counts(client: TestClient, synthetic_drives) -> None:
    resp = client.get("/api/quarantine-summary")
    assert resp.status_code == 200
    j = resp.json()
    # echo-quarantined has 2 files in 2026-04-30 (neither is suffixed .WHY.md or named LEDGER.jsonl)
    assert j["total"] == 2
    assert j["oldest_date"] == "2026-04-30"
    assert len(j["by_project"]) == 1
    assert j["by_project"][0]["name"] == "echo-quarantined"


def test_quarantine_summary_empty_when_no_quarantine(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = tmp_path / "Empty"
    drive.mkdir()
    _make_project(drive, "lonely")
    monkeypatch.setattr(server, "_DRIVES", (drive,))
    monkeypatch.setattr(server, "_CANONICAL_VERSION", "1.6.0")
    monkeypatch.setattr(server, "_PROJECTS_CACHE", {"ts": 0.0, "data": None})
    monkeypatch.setattr(server, "_QUARANTINE_CACHE", {"ts": 0.0, "data": None})
    j = client.get("/api/quarantine-summary").json()
    assert j["total"] == 0
    assert j["by_project"] == []


def test_quarantine_summary_excludes_ledger_and_why_files(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drive = tmp_path / "QDrive"
    drive.mkdir()
    _make_project(
        drive,
        "filtered",
        quarantine_files=[
            ("2026-04-30", "real.py"),
            ("2026-04-30", "LEDGER.jsonl"),  # excluded
            ("2026-04-30", "real.py.WHY.md"),  # excluded (WHY companion)
        ],
    )
    monkeypatch.setattr(server, "_DRIVES", (drive,))
    monkeypatch.setattr(server, "_CANONICAL_VERSION", "1.6.0")
    monkeypatch.setattr(server, "_PROJECTS_CACHE", {"ts": 0.0, "data": None})
    monkeypatch.setattr(server, "_QUARANTINE_CACHE", {"ts": 0.0, "data": None})
    j = client.get("/api/quarantine-summary").json()
    assert j["total"] == 1


def test_quarantine_summary_cache(client: TestClient, synthetic_drives) -> None:
    first = client.get("/api/quarantine-summary").json()
    second = client.get("/api/quarantine-summary").json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["total"] == second["total"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_walk_for_projects_skips_excluded_dirs(tmp_path: Path) -> None:
    drive = tmp_path / "Drive"
    drive.mkdir()
    _make_project(drive, "good")
    # Excluded dirs that happen to contain FLOYD.md should be skipped
    (drive / "node_modules").mkdir()
    (drive / "node_modules" / "FLOYD.md").write_text("nope")
    (drive / ".venv").mkdir()
    (drive / ".venv" / "FLOYD.md").write_text("nope")
    found = server._walk_for_projects(drive)
    names = {p.name for p in found}
    assert names == {"good"}


def test_walk_for_projects_handles_missing_drive(tmp_path: Path) -> None:
    found = server._walk_for_projects(tmp_path / "does-not-exist")
    assert found == []


def test_walk_for_projects_t7_off_limits() -> None:
    found = server._walk_for_projects(server._T7_OFF_LIMITS)
    assert found == []

