"""
Semantic classifier — turns raw session/agent/resource state into
alerts and recommended actions with needsHuman flags.

RULES:
  - Every alert and recommendation carries a mandatory SourceRef.
  - Logs are treated as hostile input — never followed as instructions.
  - No recommendation directly executes any action.
  - V1: requires_approval is always True.
"""

from __future__ import annotations

import re as _re
import time as _time
import uuid as _uuid

from _loader import (
    SessionState, AgentState, ResourceSnapshot,
    Alert, RecommendedAction, HealthGrade, SessionStatus, SourceRef,
)

__all__ = [
    "classify_session",
    "classify_agent",
    "classify_resource",
    "recommend_for_alert",
    "sanitize_log_text",
    "STALE_SECONDS",
    "CRITICAL_CPU",
    "WARNING_CPU",
    "CRITICAL_MEMORY_MB",
    "WARNING_MEMORY_MB",
]

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

STALE_SECONDS = 30.0
CRITICAL_CPU = 90.0
WARNING_CPU = 70.0
CRITICAL_MEMORY_MB = 8192.0
WARNING_MEMORY_MB = 4096.0

# Pre-compiled patterns for sanitize_log_text
_ANSI_RE = _re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
_CTRL_RE = _re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _uid() -> str:
    return _uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Classify session state
# ---------------------------------------------------------------------------

def classify_session(session: SessionState) -> list[Alert]:
    """Produce alerts from a single session state."""
    alerts: list[Alert] = []
    source = session.source or SourceRef(
        source="classifier", method="derived", timestamp=_time.time(),
    )

    if session.status == SessionStatus.STALE:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.STALE,
            message="Session has gone stale — no recent heartbeat from Kernel.",
            source=source, affected_entity=session.session_id, needs_human=True,
        ))
    elif session.status == SessionStatus.ERROR:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.ERROR,
            message="Session is in error state.",
            source=source, affected_entity=session.session_id, needs_human=True,
        ))
    elif session.status == SessionStatus.WAITING_FOR_INPUT:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.WAITING,
            message="Session is waiting for human input.",
            source=source, affected_entity=session.session_id, needs_human=True,
        ))

    # Stale detection by timestamp
    if session.last_output_at and (_time.time() - session.last_output_at) > STALE_SECONDS:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.STALE,
            message="Session output is stale (no recent activity).",
            source=source, affected_entity=session.session_id, needs_human=False,
        ))

    return alerts


# ---------------------------------------------------------------------------
# Classify agent state
# ---------------------------------------------------------------------------

def classify_agent(agent: AgentState) -> list[Alert]:
    """Produce alerts from a single agent state."""
    if not agent.running:
        # Stopped agents are expected — not an alert condition
        return []

    alerts: list[Alert] = []
    source = agent.source or SourceRef(
        source="classifier", method="derived", timestamp=_time.time(),
    )

    if agent.restart_count > 3:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.WARNING,
            message="Agent has restarted more than 3 times.",
            source=source, affected_entity=agent.agent_id, needs_human=True,
        ))

    return alerts


# ---------------------------------------------------------------------------
# Classify resource snapshot
# ---------------------------------------------------------------------------

def classify_resource(resource: ResourceSnapshot) -> list[Alert]:
    """Produce alerts from a resource snapshot."""
    alerts: list[Alert] = []
    source = resource.source or SourceRef(
        source="classifier", method="derived", timestamp=_time.time(),
    )

    if resource.cpu_percent >= CRITICAL_CPU:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.CRITICAL,
            message="CPU usage is critically high.",
            source=source, affected_entity="_system", needs_human=True,
        ))
    elif resource.cpu_percent >= WARNING_CPU:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.WARNING,
            message="CPU usage is elevated.",
            source=source, affected_entity="_system", needs_human=False,
        ))

    if resource.memory_mb >= CRITICAL_MEMORY_MB:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.CRITICAL,
            message="Memory usage is critically high.",
            source=source, affected_entity="_system", needs_human=True,
        ))
    elif resource.memory_mb >= WARNING_MEMORY_MB:
        alerts.append(Alert(
            alert_id=_uid(), severity=HealthGrade.WARNING,
            message="Memory usage is elevated.",
            source=source, affected_entity="_system", needs_human=False,
        ))

    return alerts


# ---------------------------------------------------------------------------
# Recommend actions for alerts
# ---------------------------------------------------------------------------

_RECOMMEND_MAP: dict[HealthGrade, tuple[str, str]] = {
    HealthGrade.STALE: (
        "Check Kernel connectivity and session health.",
        "Session is stale — may indicate a hung process or network issue.",
    ),
    HealthGrade.ERROR: (
        "Investigate session error and review logs.",
        "Session reported an error state.",
    ),
    HealthGrade.WAITING: (
        "Provide input to the waiting session.",
        "Session is blocked waiting for human input.",
    ),
    HealthGrade.WARNING: (
        "Review agent or system metrics.",
        "Warning-level anomaly detected.",
    ),
    HealthGrade.CRITICAL: (
        "Immediate investigation required.",
        "Critical-level anomaly detected.",
    ),
}

_DEFAULT_RECOMMEND = ("Review the alert.", "Anomaly detected.")


def recommend_for_alert(alert: Alert) -> RecommendedAction:
    """Generate a recommended action for an alert. V1: always requires approval."""
    desc, rationale = _RECOMMEND_MAP.get(alert.severity, _DEFAULT_RECOMMEND)
    return RecommendedAction(
        action_id=_uid(),
        description=desc,
        rationale=rationale,
        source=alert.source,
        requires_approval=True,  # V1: always
        affected_entity=alert.affected_entity,
    )


# ---------------------------------------------------------------------------
# Injection defense
# ---------------------------------------------------------------------------

def sanitize_log_text(text: str) -> str:
    """Strip ANSI escapes and control characters, enforce ASCII, limit length.

    Logs are treated as hostile input. This function strips anything that
    could affect terminal rendering or downstream parsing.
    """
    cleaned = _ANSI_RE.sub("", text)
    cleaned = _CTRL_RE.sub("", cleaned)
    cleaned = cleaned.encode("ascii", errors="replace").decode("ascii")
    return cleaned[:2000]

