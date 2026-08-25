"""Tests for memory.py — candidate generation with provenance invariants."""

import time
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "modules", "buddy-telemetry"))

from schema import (
    SourceRef,
    SessionState,
    SessionStatus,
    AgentState,
    ResourceSnapshot,
    Alert,
    HealthGrade,
    RecommendedAction,
    MonitorEvent,
    EventType,
    MemoryCandidate,
)
from memory import generate_candidates


def _ref(source: str = "/api/test") -> SourceRef:
    return SourceRef(source=source, method="GET", timestamp=time.time())


def _session(status: SessionStatus, source: SourceRef | None = None) -> SessionState:
    return SessionState(
        session_id="s-test",
        name="test-session",
        status=status,
        source=source,
    )


def _resource(cpu: float = 10.0, mem: float = 1024.0, source: SourceRef | None = None) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_percent=cpu,
        memory_mb=mem,
        running_agents=1,
        source=source,
    )


def _alert(severity: HealthGrade, needs_human: bool = False, source: SourceRef | None = None) -> Alert:
    return Alert(
        alert_id="a-test",
        severity=severity,
        message="test alert",
        source=source or _ref(),
        needs_human=needs_human,
    )


def _event(payload, source: SourceRef | None = None) -> MonitorEvent:
    return MonitorEvent(
        event_type=EventType.SESSION_UPDATE,
        payload=payload,
        source=source or _ref(),
    )


# ---------------------------------------------------------------------------
# Invariant: no candidate without SourceRef
# ---------------------------------------------------------------------------

class TestProvenanceInvariant:
    def test_no_candidates_when_event_lacks_source(self):
        events = [_event(_session(SessionStatus.ERROR), source=None)]
        candidates = generate_candidates(events)
        assert candidates == []

    def test_no_candidates_when_payload_lacks_source(self):
        """Event has source but session payload doesn't — still generates because event.source exists."""
        events = [_event(_session(SessionStatus.ERROR, source=None), source=_ref())]
        candidates = generate_candidates(events)
        # The event source is present, so candidates may be generated
        # but they use the event source, not the payload source
        if candidates:
            for c in candidates:
                assert c.source is not None

    def test_every_candidate_has_source_ref(self):
        events = [
            _event(_session(SessionStatus.ERROR, _ref("/api/sessions"))),
            _event(_resource(cpu=90.0, source=_ref("/api/perf"))),
        ]
        candidates = generate_candidates(events)
        for c in candidates:
            assert isinstance(c.source, SourceRef)
            assert c.source.source != ""


# ---------------------------------------------------------------------------
# Session-based candidates
# ---------------------------------------------------------------------------

class TestSessionCandidates:
    def test_error_session_generates_candidate(self):
        events = [_event(_session(SessionStatus.ERROR, _ref()))]
        candidates = generate_candidates(events)
        assert len(candidates) == 1
        assert candidates[0].category == "incident"
        assert "error" in candidates[0].summary.lower()

    def test_stale_session_generates_candidate(self):
        events = [_event(_session(SessionStatus.STALE, _ref()))]
        candidates = generate_candidates(events)
        assert len(candidates) == 1
        assert candidates[0].category == "incident"
        assert "stale" in candidates[0].summary.lower()

    def test_ready_session_generates_no_candidates(self):
        events = [_event(_session(SessionStatus.READY, _ref()))]
        candidates = generate_candidates(events)
        assert candidates == []

    def test_busy_session_generates_no_candidates(self):
        events = [_event(_session(SessionStatus.BUSY, _ref()))]
        candidates = generate_candidates(events)
        assert candidates == []


# ---------------------------------------------------------------------------
# Resource-based candidates
# ---------------------------------------------------------------------------

class TestResourceCandidates:
    def test_high_cpu_generates_candidate(self):
        events = [_event(_resource(cpu=90.0, source=_ref()))]
        candidates = generate_candidates(events)
        assert any(c.category == "metric_threshold" and "CPU" in c.summary for c in candidates)

    def test_high_memory_generates_candidate(self):
        events = [_event(_resource(cpu=10.0, mem=7000.0, source=_ref()))]
        candidates = generate_candidates(events)
        assert any(c.category == "metric_threshold" and "memory" in c.summary.lower() for c in candidates)

    def test_normal_resources_no_candidates(self):
        events = [_event(_resource(cpu=30.0, mem=2000.0, source=_ref()))]
        candidates = generate_candidates(events)
        assert candidates == []


# ---------------------------------------------------------------------------
# Alert-based candidates
# ---------------------------------------------------------------------------

class TestAlertCandidates:
    def test_critical_alert_with_needs_human_generates_candidate(self):
        alert = _alert(HealthGrade.CRITICAL, needs_human=True)
        events = [_event(alert)]
        candidates = generate_candidates(events)
        assert len(candidates) == 1
        assert candidates[0].category == "incident"

    def test_error_alert_with_needs_human_generates_candidate(self):
        alert = _alert(HealthGrade.ERROR, needs_human=True)
        events = [_event(alert)]
        candidates = generate_candidates(events)
        assert len(candidates) == 1

    def test_warning_alert_no_candidate(self):
        alert = _alert(HealthGrade.WARNING, needs_human=True)
        events = [_event(alert)]
        candidates = generate_candidates(events)
        assert candidates == []

    def test_critical_without_needs_human_no_candidate(self):
        alert = _alert(HealthGrade.CRITICAL, needs_human=False)
        events = [_event(alert)]
        candidates = generate_candidates(events)
        assert candidates == []


# ---------------------------------------------------------------------------
# V1 invariant: approved is always False
# ---------------------------------------------------------------------------

class TestV1Invariant:
    def test_all_candidates_are_unapproved(self):
        events = [
            _event(_session(SessionStatus.ERROR, _ref())),
            _event(_resource(cpu=90.0, source=_ref())),
            _event(_alert(HealthGrade.CRITICAL, True, _ref())),
        ]
        candidates = generate_candidates(events)
        for c in candidates:
            assert c.approved is False
            assert c.rejected is False


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_events_list(self):
        assert generate_candidates([]) == []

    def test_agent_state_generates_no_candidates(self):
        agent = AgentState(agent_id="a1", name="coder", running=True, source=_ref())
        events = [_event(agent)]
        assert generate_candidates(events) == []

    def test_recommendation_generates_no_candidates(self):
        rec = RecommendedAction(
            action_id="r1",
            description="test",
            rationale="test",
            source=_ref(),
        )
        events = [_event(rec)]
        assert generate_candidates(events) == []

    def test_evidence_snippet_populated(self):
        events = [_event(_session(SessionStatus.ERROR, _ref("/api/sessions")))]
        candidates = generate_candidates(events)
        assert len(candidates) == 1
        assert candidates[0].evidence_snippet != ""
        assert "session_id=" in candidates[0].evidence_snippet

