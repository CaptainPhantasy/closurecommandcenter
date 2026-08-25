"""
Normalizer — converts raw Kernel API responses into canonical schema types.

Every function takes raw dict input and returns a schema dataclass with
an attached SourceRef. Handles missing/malformed fields gracefully by
falling back to UNKNOWN/zero defaults rather than raising.
"""

from __future__ import annotations

import time
from typing import Optional

from _loader import SessionState, SessionStatus, AgentState, ResourceSnapshot, SourceRef

__all__ = [
    "normalize_session_status",
    "normalize_aterm_session",
    "normalize_aterm_status",
    "normalize_agent",
    "normalize_agents",
    "normalize_performance",
]


def _safe_float(val: object, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: object, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _source(endpoint: str, method: str = "GET", ts: Optional[float] = None) -> SourceRef:
    return SourceRef(source=endpoint, method=method, timestamp=ts or time.time())


def normalize_session_status(raw: str) -> SessionStatus:
    """Map raw status string to canonical enum; unknown strings become UNKNOWN."""
    if not isinstance(raw, str):
        return SessionStatus.UNKNOWN
    mapping = {
        "ready": SessionStatus.READY,
        "running": SessionStatus.BUSY,
        "busy": SessionStatus.BUSY,
        "waiting_for_input": SessionStatus.WAITING_FOR_INPUT,
        "waiting": SessionStatus.WAITING_FOR_INPUT,
        "error": SessionStatus.ERROR,
        "stopped": SessionStatus.STALE,
        "stale": SessionStatus.STALE,
    }
    return mapping.get(raw.strip().lower(), SessionStatus.UNKNOWN)


def normalize_aterm_session(raw: dict, source: Optional[SourceRef] = None) -> SessionState:
    """Convert a single session from /api/aterm/status into SessionState."""
    return SessionState(
        session_id=raw.get("id", raw.get("name", "unknown")),
        name=raw.get("name", "unknown"),
        status=normalize_session_status(raw.get("status", "unknown")),
        agent_count=_safe_int(raw.get("agent_count")),
        last_output_at=_safe_float(raw.get("last_output_at")) or None,
        pid=raw.get("pid"),
        uptime_seconds=_safe_float(raw.get("uptime_seconds")),
        source=source or _source("/api/aterm/status"),
    )


def normalize_aterm_status(raw: dict) -> list[SessionState]:
    """Convert full /api/aterm/status response into list of SessionState."""
    source = _source("/api/aterm/status")
    sessions = raw.get("sessions", [])
    return [normalize_aterm_session(s, source) for s in sessions]


def normalize_agent(raw: dict, source: Optional[SourceRef] = None) -> AgentState:
    """Convert a single agent from /api/agents into AgentState."""
    return AgentState(
        agent_id=raw.get("id", "unknown"),
        name=raw.get("name", "unknown"),
        running=bool(raw.get("running", False)),
        command=raw.get("command", ""),
        tags=raw.get("tags", []),
        restart_count=_safe_int(raw.get("restart_count")),
        total_uptime=_safe_float(raw.get("total_uptime")),
        source=source or _source("/api/agents"),
    )


def normalize_agents(raw: list[dict]) -> list[AgentState]:
    """Convert /api/agents response into list of AgentState."""
    source = _source("/api/agents")
    return [normalize_agent(a, source) for a in raw]


def normalize_performance(raw: dict) -> ResourceSnapshot:
    """Convert /api/performance response into ResourceSnapshot."""
    return ResourceSnapshot(
        cpu_percent=_safe_float(raw.get("cpu_percent")),
        memory_mb=_safe_float(raw.get("memory_mb")),
        running_agents=_safe_int(raw.get("running_agents")),
        timestamp=_safe_float(raw.get("timestamp")) or time.time(),
        source=_source("/api/performance"),
    )

