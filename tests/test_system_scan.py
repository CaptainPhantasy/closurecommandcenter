"""Tests for scripts/system_scan.py — the deterministic Mac System Health scan.

Authority: plans/controlboard.md Step 12
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import system_scan as ss  # noqa: E402


# Bypass conftest's autouse server-required fixture
@pytest.fixture(scope="module", autouse=True)
def _ensure_server_up():  # type: ignore[no-redef]
    yield


def _make_app_bundle(parent: Path, name: str, *, exe_size: int = 4096, atime_days_ago: int = 0) -> Path:
    """Build a minimal .app bundle structure under `parent`."""
    bundle = parent / f"{name}.app"
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    exe = macos / name.lower().replace(" ", "_")
    exe.write_bytes(b"\x00" * exe_size)
    if atime_days_ago > 0:
        target = (datetime.now(timezone.utc) - timedelta(days=atime_days_ago)).timestamp()
        import os
        os.utime(exe, (target, target))
    return bundle


# ---------------------------------------------------------------------------
# Pure unit tests
# ---------------------------------------------------------------------------

def test_classify_app_keep_when_no_idle_data() -> None:
    rec, _ = ss._classify_app(500.0, days_idle=None)
    assert rec == "keep"


def test_classify_app_remove_when_large_and_idle() -> None:
    rec, notes = ss._classify_app(500.0, days_idle=120)
    assert rec == "remove"
    assert "120d" in notes


def test_classify_app_consider_for_moderate_idle() -> None:
    rec, _ = ss._classify_app(50.0, days_idle=60)
    assert rec == "consider"


def test_classify_app_keep_for_recent_use() -> None:
    rec, _ = ss._classify_app(200.0, days_idle=5)
    assert rec == "keep"


def test_classify_hog_system_processes() -> None:
    assert ss._classify_hog("kernel_task") == "system"
    assert ss._classify_hog("WindowServer") == "system"
    assert ss._classify_hog("launchd") == "system"


def test_classify_hog_chrome_tame_after_bucket_rename() -> None:
    """'Google Chrome (incl. helpers)' is the post-bucketing form."""
    assert ss._classify_hog("Google Chrome (incl. helpers)") == "tame"
    assert ss._classify_hog("Google Chrome") == "tame"


def test_classify_hog_unknown_defaults_to_consider() -> None:
    assert ss._classify_hog("SomeRandomProcess") == "consider"


def test_days_since_iso_rounding() -> None:
    iso = (datetime.now(timezone.utc) - timedelta(days=10, hours=5)).isoformat()
    days = ss._days_since_iso(iso)
    assert days in (10, 11)  # tolerate edge of day-rollover


def test_days_since_iso_handles_none() -> None:
    assert ss._days_since_iso(None) is None


def test_days_since_iso_handles_garbage() -> None:
    assert ss._days_since_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# scan_apps with a synthetic /Applications fixture
# ---------------------------------------------------------------------------


def test_scan_apps_returns_entries_for_synthetic_bundles(tmp_path: Path) -> None:
    _make_app_bundle(tmp_path, "Recent", exe_size=8192)
    _make_app_bundle(tmp_path, "OldHog", exe_size=200 * 1024 * 1024, atime_days_ago=120)
    apps = ss.scan_apps([tmp_path])
    names = {a.name for a in apps}
    assert names == {"Recent", "OldHog"}


def test_scan_apps_old_hog_recommendation_is_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic .app bundles inside tmp_path get a creation-time Spotlight stamp,
    so we patch mdls to return None and exercise the executable-mtime fallback path
    that production also uses for apps Spotlight has no metadata for.
    """
    monkeypatch.setattr(ss, "_mdls_last_used", lambda _path: None)
    _make_app_bundle(tmp_path, "OldHog", exe_size=200 * 1024 * 1024, atime_days_ago=120)
    apps = ss.scan_apps([tmp_path])
    hog = next(a for a in apps if a.name == "OldHog")
    assert hog.size_mb >= 100, f"expected ≥100 MB, got {hog.size_mb}"
    assert hog.days_idle is not None
    assert hog.days_idle >= 90
    assert hog.recommendation == "remove"


def test_scan_apps_skips_non_app_entries(tmp_path: Path) -> None:
    (tmp_path / "README.txt").write_text("not an app")
    (tmp_path / "subdir").mkdir()
    _make_app_bundle(tmp_path, "Real")
    apps = ss.scan_apps([tmp_path])
    assert {a.name for a in apps} == {"Real"}


def test_scan_apps_handles_missing_dir() -> None:
    apps = ss.scan_apps([Path("/does/not/exist")])
    assert apps == []


# ---------------------------------------------------------------------------
# scan_disk_recovery_candidates filtering
# ---------------------------------------------------------------------------


def test_scan_disk_recovery_candidates_filters_and_sorts() -> None:
    apps = [
        ss.AppEntry(name="A", path="/A", size_mb=500, last_used_iso=None, days_idle=120, recommendation="remove"),
        ss.AppEntry(name="B", path="/B", size_mb=2000, last_used_iso=None, days_idle=200, recommendation="remove"),
        ss.AppEntry(name="C", path="/C", size_mb=100, last_used_iso=None, days_idle=10, recommendation="keep"),
        ss.AppEntry(name="D", path="/D", size_mb=50, last_used_iso=None, days_idle=None, recommendation="keep"),
    ]
    cands = ss.scan_disk_recovery_candidates(apps)
    assert [c.name for c in cands] == ["B", "A"]


def test_scan_disk_recovery_candidates_top_n() -> None:
    apps = [
        ss.AppEntry(name=f"app{i}", path=f"/p{i}", size_mb=float(100 * i),
                    last_used_iso=None, days_idle=120, recommendation="remove")
        for i in range(1, 25)
    ]
    cands = ss.scan_disk_recovery_candidates(apps, top_n=10)
    assert len(cands) == 10
    # Largest first
    assert cands[0].name == "app24"
    assert cands[-1].name == "app15"


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------


def test_cache_path_is_under_floyd() -> None:
    p = ss.cache_path()
    assert p.name == "system-health-cache.json"
    assert p.parent.name == ".floyd"
    assert p.parent.parent == Path(ss.__file__).resolve().parent.parent


def test_write_and_read_cache_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / ".floyd" / "system-health-cache.json"
    monkeypatch.setattr(ss, "cache_path", lambda: cache)
    report = ss.SystemHealthReport(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=1.5, stats={}, apps=[], memory_hogs=[],
        disk_recovery_candidates=[], errors=[],
    )
    written = ss.write_cache(report)
    assert written.is_file()
    loaded = ss.read_cache()
    assert loaded is not None
    assert loaded["duration_seconds"] == 1.5
    assert ss.cache_is_fresh(60) is True


def test_cache_is_fresh_returns_false_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "no-such-cache.json"
    monkeypatch.setattr(ss, "cache_path", lambda: cache)
    assert ss.cache_is_fresh(60) is False
    assert ss.read_cache() is None


# ---------------------------------------------------------------------------
# build_report end-to-end (integration-light: real ps + real du on tmp_path)
# ---------------------------------------------------------------------------


def test_build_report_runs_end_to_end(tmp_path: Path) -> None:
    _make_app_bundle(tmp_path, "Demo", exe_size=2 * 1024 * 1024, atime_days_ago=120)
    report = ss.build_report(app_dirs=[tmp_path])
    assert report.scanned_at  # ISO timestamp
    assert report.duration_seconds >= 0
    assert report.stats["total_apps_scanned"] == 1
    # Memory scan runs against the real machine, but should always return ≥0 hogs
    assert isinstance(report.memory_hogs, list)
    payload = json.loads(report.to_json())
    assert "stats" in payload
    assert "apps" in payload

