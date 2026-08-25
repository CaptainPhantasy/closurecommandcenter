"""
Read-only adapters for Kernel API endpoints.

Each adapter makes a GET request to a Kernel endpoint, normalizes the response,
and attaches SourceRef provenance. All methods are read-only — no POST/PUT/DELETE.

If the Kernel is unreachable or returns malformed data, adapters return stale
placeholder state rather than raising, so the spine never crashes.
"""

from __future__ import annotations

import json as _json
import time as _time
import urllib.request as _urllib_request
import urllib.error as _urllib_error
from typing import Optional

from _loader import (
    SessionState, AgentState, ResourceSnapshot, SessionStatus, SourceRef,
)
from normalizer import normalize_aterm_status, normalize_agents, normalize_performance

__all__ = [
    "fetch_sessions",
    "fetch_agents",
    "fetch_performance",
    "fetch_health",
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 11527
DEFAULT_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Low-level HTTP GET
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[Optional[dict], float]:
    """GET a JSON endpoint. Returns (parsed_json, elapsed_seconds) or (None, elapsed)."""
    start = _time.monotonic()
    try:
        req = _urllib_request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            elapsed = _time.monotonic() - start
            return _json.loads(body), elapsed
    except (_urllib_error.URLError, _json.JSONDecodeError, OSError):
        elapsed = _time.monotonic() - start
        return None, elapsed


# ---------------------------------------------------------------------------
# Stale marker helpers
# ---------------------------------------------------------------------------

def _stale_sessions(count: int = 1) -> list[SessionState]:
    """Produce stale placeholder sessions when the Kernel is unreachable."""
    source = SourceRef(source="/api/aterm/status", method="GET", timestamp=_time.time())
    return [
        SessionState(
            session_id=f"_stale_{i}",
            name=f"(unreachable-{i})",
            status=SessionStatus.STALE,
            source=source,
        )
        for i in range(count)
    ]


def _stale_resources() -> ResourceSnapshot:
    source = SourceRef(source="/api/performance", method="GET", timestamp=_time.time())
    return ResourceSnapshot(cpu_percent=0.0, memory_mb=0.0, running_agents=0, source=source)


# ---------------------------------------------------------------------------
# Public adapter functions — all read-only
# ---------------------------------------------------------------------------

def fetch_sessions(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[SessionState]:
    """Fetch all ATerm sessions via /api/aterm/status. Returns stale on failure."""
    url = f"http://{host}:{port}/api/aterm/status"
    data, _ = _http_get(url, timeout)
    if data is None:
        return _stale_sessions()
    try:
        return normalize_aterm_status(data)
    except Exception:
        return _stale_sessions()


def fetch_agents(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[AgentState]:
    """Fetch all agents via /api/agents. Returns empty list on failure."""
    url = f"http://{host}:{port}/api/agents"
    data, _ = _http_get(url, timeout)
    if data is None:
        source = SourceRef(source="/api/agents", method="GET", timestamp=_time.time())
        return [AgentState(agent_id="_stale", name="(unreachable)", running=False, source=source)]
    try:
        agents = data if isinstance(data, list) else data.get("agents", data.get("items", []))
        return normalize_agents(agents if isinstance(agents, list) else [])
    except Exception:
        return []


def fetch_performance(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ResourceSnapshot:
    """Fetch system performance via /api/performance. Returns stale on failure."""
    url = f"http://{host}:{port}/api/performance"
    data, _ = _http_get(url, timeout)
    if data is None:
        return _stale_resources()
    try:
        return normalize_performance(data)
    except Exception:
        return _stale_resources()


def fetch_health(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Check /api/health. Returns True if 200, False otherwise."""
    url = f"http://{host}:{port}/api/health"
    data, _ = _http_get(url, timeout)
    return data is not None and data.get("status") == "ok"

