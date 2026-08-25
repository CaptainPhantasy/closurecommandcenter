#!/usr/bin/env python3
"""Mac System Health scan — authoritative, deterministic re-implementation
of the original one-shot mac-cleanup-report.html scan.

Authority: plans/controlboard.md Step 12

Usage:
    python scripts/system_scan.py [--write]

When --write is set (default), the result is written to:
    control-center/.floyd/system-health-cache.json

The /api/system-health endpoint reads from the cache; this script is the
producer. A scan typically completes in under ten seconds on a modern Mac.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tunables (per plans/controlboard.md and the original cleanup report)
# ---------------------------------------------------------------------------

# Apps idle longer than this are flagged as removal candidates.
IDLE_REMOVE_DAYS = 90
IDLE_CONSIDER_DAYS = 30

# Memory hogs to specifically classify (per Step 12 plan):
#   "tame" — actively bloated, take action
#   "consider" — moderately heavy, review
#   "keep" — known-good, leave alone
#   "system" — kernel/OS, never touch
HOG_CLASSIFICATION_RULES: list[tuple[str, str]] = [
    # (regex pattern, classification) — applied in order; first match wins.
    # Names are post-bucketing, so "Google Chrome (incl. helpers)" matches the
    # "Google Chrome" prefix.
    (r"^kernel_task$|^launchd$|^WindowServer$|^mds$|^mds_stores$|^coreaudiod$|^syslogd$", "system"),
    (r"^Google Chrome\b", "tame"),
    (r"^OpenCode$|^opencode$", "tame"),
    (r"^Notion\b", "consider"),
    (r"^superfloyd_|^floyd-lab-server$|^floyd4$|^floyd_", "consider"),
    (r"^Claude\b|^claude$", "keep"),
    (r"^Code\b", "keep"),
    (r"^node$|^bun$|^python\d?$|^ruby$", "consider"),
    (r"^Slack\b|^Discord\b|^Spotify\b", "consider"),
]


# ---------------------------------------------------------------------------
# Data shapes (frozen — schema stability matters here too)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppEntry:
    name: str
    path: str
    size_mb: float
    last_used_iso: str | None
    days_idle: int | None
    recommendation: str   # keep | consider | remove
    notes: str = ""


@dataclass(frozen=True)
class MemoryHog:
    process_name: str
    pid_count: int
    rss_gb: float
    pct_total: float
    classification: str   # system | keep | consider | tame


@dataclass(frozen=True)
class DiskRecoveryCandidate:
    name: str
    size_mb: float
    days_idle: int | None
    path: str


@dataclass(frozen=True)
class SystemStats:
    total_apps_scanned: int
    total_recoverable_gb: float
    memory_total_gb: float
    memory_used_gb: float
    memory_free_gb: float
    top_hog: str
    idle_app_count: int


@dataclass
class SystemHealthReport:
    scanned_at: str
    duration_seconds: float
    stats: dict
    apps: list[dict]
    memory_hogs: list[dict]
    disk_recovery_candidates: list[dict]
    errors: list[str] = field(default_factory=list)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(asdict(self), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 20) -> str:
    """Run a command, capture stdout, return decoded text. Empty string on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _du_mb(path: Path) -> float:
    """Return directory size in MB via `du -sk` (kilobyte blocks)."""
    out = _run(["du", "-sk", str(path)], timeout=30)
    if not out:
        return 0.0
    try:
        kb = int(out.split()[0])
        return round(kb / 1024.0, 1)
    except (ValueError, IndexError):
        return 0.0


def _mdls_last_used(path: Path) -> str | None:
    """Read kMDItemLastUsedDate via mdls. Returns ISO timestamp or None."""
    out = _run(["mdls", "-name", "kMDItemLastUsedDate", "-raw", str(path)], timeout=5)
    out = out.strip()
    if not out or out == "(null)":
        return None
    # mdls raw format: 2026-04-15 14:23:11 +0000
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", out)
    if not m:
        return None
    return f"{m.group(1)}T{m.group(2)}+00:00"


def _executable_mtime(path: Path) -> str | None:
    """Fallback when Spotlight has no kMDItemLastUsedDate: read modification
    time on the bundle's main executable. mtime survives `du` and other
    metadata-touch operations, so it's a stable proxy for 'last app update'.
    """
    try:
        macos = path / "Contents" / "MacOS"
        if not macos.is_dir():
            return None
        children = sorted(macos.iterdir(), key=lambda p: p.stat().st_size, reverse=True)
        if not children:
            return None
        mtime = children[0].stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except (OSError, IndexError):
        return None


def _resolve_last_used(path: Path) -> str | None:
    """Try mdls first, fall back to the executable's mtime."""
    return _mdls_last_used(path) or _executable_mtime(path)


def _days_since_iso(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).days)
    except (ValueError, TypeError):
        return None


def _classify_app(size_mb: float, days_idle: int | None) -> tuple[str, str]:
    """Returns (recommendation, notes)."""
    if days_idle is None:
        return ("keep", "no last-used timestamp")
    if days_idle >= IDLE_REMOVE_DAYS and size_mb >= 100:
        return ("remove", f"idle {days_idle}d, {size_mb:.0f} MB recoverable")
    if days_idle >= IDLE_CONSIDER_DAYS:
        return ("consider", f"idle {days_idle}d")
    return ("keep", f"used {days_idle}d ago")


def _classify_hog(process_name: str) -> str:
    for pattern, label in HOG_CLASSIFICATION_RULES:
        if re.search(pattern, process_name):
            return label
    return "consider"  # default for unknown heavy processes


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------


def scan_apps(app_dirs: list[Path] | None = None) -> list[AppEntry]:
    """Scan /Applications and ~/Applications for .app bundles."""
    if app_dirs is None:
        app_dirs = [Path("/Applications"), Path.home() / "Applications"]
    found: list[AppEntry] = []
    for adir in app_dirs:
        if not adir.is_dir():
            continue
        for entry in sorted(adir.iterdir()):
            if not entry.name.endswith(".app"):
                continue
            size_mb = _du_mb(entry)
            last_used = _resolve_last_used(entry)
            days_idle = _days_since_iso(last_used)
            rec, notes = _classify_app(size_mb, days_idle)
            found.append(AppEntry(
                name=entry.stem,
                path=str(entry),
                size_mb=size_mb,
                last_used_iso=last_used,
                days_idle=days_idle,
                recommendation=rec,
                notes=notes,
            ))
    return found


def scan_memory() -> list[MemoryHog]:
    """Aggregate `ps -axco pid,rss,pcpu,comm` by process name."""
    out = _run(["ps", "-axco", "pid,rss,pcpu,comm"], timeout=10)
    if not out:
        return []
    lines = out.strip().splitlines()
    if len(lines) < 2:
        return []
    # Header is first line; data rows below.
    by_name: dict[str, dict[str, float]] = {}
    for raw in lines[1:]:
        parts = raw.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            rss_kb = int(parts[1])
        except ValueError:
            continue
        name = parts[3]
        # Normalize Helper subnames into a single bucket per app
        bucket = re.sub(r" Helper.*$", " (incl. helpers)", name)
        agg = by_name.setdefault(bucket, {"pid_count": 0, "rss_kb": 0.0})
        agg["pid_count"] += 1
        agg["rss_kb"] += rss_kb

    total_kb = sum(v["rss_kb"] for v in by_name.values()) or 1.0
    rows: list[MemoryHog] = []
    for name, agg in by_name.items():
        rss_gb = round(agg["rss_kb"] / 1024.0 / 1024.0, 2)
        if rss_gb < 0.05:
            continue  # ignore <50MB processes
        rows.append(MemoryHog(
            process_name=name,
            pid_count=int(agg["pid_count"]),
            rss_gb=rss_gb,
            pct_total=round(agg["rss_kb"] / total_kb * 100.0, 1),
            classification=_classify_hog(name),
        ))
    rows.sort(key=lambda r: -r.rss_gb)
    return rows[:25]


def scan_disk_recovery_candidates(
    apps: list[AppEntry], idle_days: int = IDLE_REMOVE_DAYS, top_n: int = 15
) -> list[DiskRecoveryCandidate]:
    """Pick apps idle >= idle_days, sort by size desc, take top_n."""
    candidates = [
        DiskRecoveryCandidate(
            name=a.name, size_mb=a.size_mb,
            days_idle=a.days_idle, path=a.path,
        )
        for a in apps
        if a.days_idle is not None and a.days_idle >= idle_days
    ]
    candidates.sort(key=lambda c: -c.size_mb)
    return candidates[:top_n]


def _read_total_mem_gb() -> float:
    out = _run(["sysctl", "-n", "hw.memsize"], timeout=2)
    try:
        return round(int(out.strip()) / (1024 ** 3), 1)
    except (ValueError, TypeError):
        return 0.0


def _read_used_mem_gb() -> float:
    """Approximate used memory from `vm_stat` (active + wired + compressed)."""
    out = _run(["vm_stat"], timeout=5)
    if not out:
        return 0.0
    page_size_match = re.search(r"page size of (\d+) bytes", out)
    page_size = int(page_size_match.group(1)) if page_size_match else 4096
    fields = {
        "active": r"Pages active:\s+(\d+)",
        "wired": r"Pages wired down:\s+(\d+)",
        "compressed": r"Pages occupied by compressor:\s+(\d+)",
    }
    total_pages = 0
    for pat in fields.values():
        m = re.search(pat, out)
        if m:
            total_pages += int(m.group(1))
    return round(total_pages * page_size / (1024 ** 3), 1)


def build_report(app_dirs: list[Path] | None = None) -> SystemHealthReport:
    """Run every scanner and return a populated report."""
    started = datetime.now(timezone.utc)
    errors: list[str] = []

    if not shutil.which("du") or not shutil.which("ps"):
        errors.append("required system utilities (du / ps) not on PATH")

    try:
        apps = scan_apps(app_dirs)
    except Exception as exc:  # pragma: no cover — defensive
        errors.append(f"scan_apps failed: {exc}")
        apps = []

    try:
        hogs = scan_memory()
    except Exception as exc:  # pragma: no cover
        errors.append(f"scan_memory failed: {exc}")
        hogs = []

    candidates = scan_disk_recovery_candidates(apps)

    total_recoverable = round(sum(c.size_mb for c in candidates) / 1024.0, 1)
    mem_total = _read_total_mem_gb()
    mem_used = _read_used_mem_gb()
    mem_free = round(max(0.0, mem_total - mem_used), 1)
    top_hog = hogs[0].process_name if hogs else "(none)"

    stats = SystemStats(
        total_apps_scanned=len(apps),
        total_recoverable_gb=total_recoverable,
        memory_total_gb=mem_total,
        memory_used_gb=mem_used,
        memory_free_gb=mem_free,
        top_hog=top_hog,
        idle_app_count=len(candidates),
    )

    finished = datetime.now(timezone.utc)
    duration = (finished - started).total_seconds()

    return SystemHealthReport(
        scanned_at=finished.isoformat(),
        duration_seconds=round(duration, 2),
        stats=asdict(stats),
        apps=[asdict(a) for a in apps],
        memory_hogs=[asdict(h) for h in hogs],
        disk_recovery_candidates=[asdict(c) for c in candidates],
        errors=errors,
    )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def cache_path() -> Path:
    here = Path(__file__).resolve().parent.parent
    return here / ".floyd" / "system-health-cache.json"


def write_cache(report: SystemHealthReport) -> Path:
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(report.to_json())
    return p


def read_cache() -> dict[str, Any] | None:
    p = cache_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def cache_is_fresh(max_age_seconds: int) -> bool:
    p = cache_path()
    if not p.is_file():
        return False
    age = datetime.now(timezone.utc).timestamp() - p.stat().st_mtime
    return age <= max_age_seconds


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mac System Health scan")
    parser.add_argument("--write", action="store_true", default=True,
                        help="Write the report to .floyd/system-health-cache.json (default)")
    parser.add_argument("--no-write", dest="write", action="store_false",
                        help="Print to stdout only")
    parser.add_argument("--app-dir", action="append", default=None,
                        help="Override app scan directories (repeatable)")
    args = parser.parse_args(argv)

    app_dirs = [Path(p) for p in args.app_dir] if args.app_dir else None
    report = build_report(app_dirs=app_dirs)
    if args.write:
        path = write_cache(report)
        print(f"[system_scan] wrote {path}")
        print(f"[system_scan] apps={len(report.apps)} hogs={len(report.memory_hogs)} "
              f"recoverable={report.stats['total_recoverable_gb']} GB "
              f"duration={report.duration_seconds}s")
    else:
        print(report.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())

