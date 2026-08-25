"""
Canonical event/state type definitions for the F.U.C.K.B.U.T.T. read-only spine.

Every type is a plain dataclass — no Pydantic dependency, no FastAPI coupling.
Pydantic adapters live in normalizer.py for API boundary serialization only.

INVARIANTS:
  - SourceRef is frozen (immutable provenance pointer).
  - Every derived Alert/RecommendedAction carries a mandatory SourceRef.
  - RecommendedAction.requires_approval is always True in V1.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "SourceRef",
    "SessionStatus",
    "HealthGrade",
    "SessionState",
    "AgentState",
    "TaskState",
    "ResourceSnapshot",
    "Alert",
    "RecommendedAction",
    "EventType",
    "MonitorEvent",
    "MemoryCandidate",
]
# ---------------------------------------------------------------------------
# SourceRef — provenance attached to every derived value
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRef:
    """Immutable provenance pointer. Required on every derived event/alert/recommendation."""
    source: str          # e.g. "/api/aterm/status", "/api/performance"
    method: str          # "GET", "SSE", "WS", "file"
    timestamp: float     # epoch seconds when the raw data was observed
    host: str = "localhost"
    port: int = 11527


# ---------------------------------------------------------------------------
# Canonical state enums
# ---------------------------------------------------------------------------

class SessionStatus(str, Enum):
    READY = "ready"
    BUSY = "busy"
    WAITING_FOR_INPUT = "waiting_for_input"
    ERROR = "error"
    STALE = "stale"
    UNKNOWN = "unknown"


class HealthGrade(str, Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    BLOCKED = "blocked"
    WAITING = "waiting_for_input"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Core state models (raw adapter output)
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    session_id: str
    name: str
    status: SessionStatus
    agent_count: int = 0
    last_output_at: Optional[float] = None
    pid: Optional[int] = None
    uptime_seconds: float = 0.0
    source: Optional[SourceRef] = None


@dataclass
class AgentState:
    agent_id: str
    name: str
    running: bool
    command: str = ""
    tags: list[str] = field(default_factory=list)
    restart_count: int = 0
    total_uptime: float = 0.0
    source: Optional[SourceRef] = None


@dataclass
class TaskState:
    task_id: str
    session_name: str
    description: str = ""
    status: str = "unknown"
    source: Optional[SourceRef] = None


@dataclass
class ResourceSnapshot:
    cpu_percent: float
    memory_mb: float
    running_agents: int
    timestamp: float = field(default_factory=time.time)
    source: Optional[SourceRef] = None


# ---------------------------------------------------------------------------
# Derived types (produced by classifier, not raw adapters)
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    alert_id: str
    severity: HealthGrade
    message: str
    source: SourceRef          # always required for derived values
    affected_entity: str = ""
    needs_human: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class RecommendedAction:
    action_id: str
    description: str
    rationale: str
    source: SourceRef          # always required
    requires_approval: bool = True  # V1: always True, no auto-execution
    affected_entity: str = ""
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# MonitorEvent — unified envelope for the event spine
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    SESSION_UPDATE = "session_update"
    AGENT_UPDATE = "agent_update"
    RESOURCE_UPDATE = "resource_update"
    ALERT = "alert"
    RECOMMENDATION = "recommendation"
    MEMORY_CANDIDATE = "memory_candidate"


@dataclass
class MemoryCandidate:
    """Derived observation proposed for long-term memory storage.

    INVARIANTS:
      - source is always required (no candidate without provenance).
      - approved defaults to False — V1 has no auto-approve path.
      - category classifies the memory type for downstream routing.
    """
    candidate_id: str
    summary: str
    category: str               # e.g. "pattern", "incident", "metric_threshold", "behavior"
    source: SourceRef           # always required
    evidence_snippet: str = ""  # short excerpt justifying the candidate
    approved: bool = False      # V1: always False until human reviews
    rejected: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class MonitorEvent:
    event_type: EventType
    payload: SessionState | AgentState | ResourceSnapshot | Alert | RecommendedAction | MemoryCandidate
    source: SourceRef
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
