"""
Memory candidate generator for the F.U.C.K.B.U.T.T. read-only spine.

Generates MemoryCandidate objects from classified events. V1 invariants:
  - Every candidate carries a mandatory SourceRef (no candidate without provenance).
  - approved is always False — no auto-approve path exists.
  - Candidates are proposals only — no automatic memory writes.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from schema import (
    Alert,
    HealthGrade,
    MemoryCandidate,
    MonitorEvent,
    RecommendedAction,
    ResourceSnapshot,
    SessionState,
    SourceRef,
)

__all__ = ["generate_candidates", "CandidateRule"]


def _uid() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Candidate rules — pattern-match against event state to propose memories
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateRule:
    """A single rule that may produce a memory candidate from an event."""
    name: str
    category: str
    description_template: str
    min_occurrences: int = 1  # how many times pattern seen before proposing


# Pre-compiled patterns for candidate detection
_ERROR_PATTERN = re.compile(r"error|fail|crash|timeout|exception", re.IGNORECASE)
_STALE_PATTERN = re.compile(r"stale|unresponsive|no response", re.IGNORECASE)
_RESOURCE_SPIKE_PATTERN = re.compile(r"cpu|memory|resource", re.IGNORECASE)

# Thresholds for resource-based candidates
_HIGH_CPU_THRESHOLD = 85.0
_HIGH_MEMORY_MB_THRESHOLD = 6144.0


def _session_candidates(session: SessionState) -> list[MemoryCandidate]:
    """Generate candidates from session state transitions."""
    candidates: list[MemoryCandidate] = []

    if session.status.value == "error" and session.source:
        candidates.append(MemoryCandidate(
            candidate_id=_uid(),
            summary=f"Session '{session.name}' entered error state",
            category="incident",
            source=session.source,
            evidence_snippet=f"session_id={session.session_id} status=error",
        ))

    if session.status.value == "stale" and session.source:
        candidates.append(MemoryCandidate(
            candidate_id=_uid(),
            summary=f"Session '{session.name}' went stale",
            category="incident",
            source=session.source,
            evidence_snippet=f"session_id={session.session_id} status=stale last_output={session.last_output_at}",
        ))

    return candidates


def _resource_candidates(resource: ResourceSnapshot) -> list[MemoryCandidate]:
    """Generate candidates from resource anomalies."""
    candidates: list[MemoryCandidate] = []

    if resource.cpu_percent >= _HIGH_CPU_THRESHOLD and resource.source:
        candidates.append(MemoryCandidate(
            candidate_id=_uid(),
            summary=f"Sustained high CPU: {resource.cpu_percent:.1f}%",
            category="metric_threshold",
            source=resource.source,
            evidence_snippet=f"cpu_percent={resource.cpu_percent:.1f} running_agents={resource.running_agents}",
        ))

    if resource.memory_mb >= _HIGH_MEMORY_MB_THRESHOLD and resource.source:
        candidates.append(MemoryCandidate(
            candidate_id=_uid(),
            summary=f"High memory usage: {resource.memory_mb:.0f} MB",
            category="metric_threshold",
            source=resource.source,
            evidence_snippet=f"memory_mb={resource.memory_mb:.0f}",
        ))

    return candidates


def _alert_candidates(alert: Alert) -> list[MemoryCandidate]:
    """Generate candidates from high-severity alerts."""
    candidates: list[MemoryCandidate] = []

    if alert.severity in (HealthGrade.CRITICAL, HealthGrade.ERROR) and alert.needs_human:
        candidates.append(MemoryCandidate(
            candidate_id=_uid(),
            summary=alert.message,
            category="incident",
            source=alert.source,
            evidence_snippet=f"alert_id={alert.alert_id} severity={alert.severity.value} entity={alert.affected_entity}",
        ))

    return candidates


def generate_candidates(events: list[MonitorEvent]) -> list[MemoryCandidate]:
    """Scan a list of events and produce memory candidates.

    Every returned candidate has a non-None SourceRef. If an event lacks
    a source, it is silently skipped — no candidate without provenance.
    """
    candidates: list[MemoryCandidate] = []

    for event in events:
        # SourceRef is mandatory — skip events without it
        if not event.source:
            continue

        payload = event.payload

        if isinstance(payload, SessionState):
            candidates.extend(_session_candidates(payload))

        elif isinstance(payload, ResourceSnapshot):
            candidates.extend(_resource_candidates(payload))

        elif isinstance(payload, Alert):
            candidates.extend(_alert_candidates(payload))

        # RecommendedAction and AgentState do not produce memory candidates in V1

    return candidates

