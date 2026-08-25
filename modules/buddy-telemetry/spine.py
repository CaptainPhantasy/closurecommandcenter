"""
Bounded append-only event buffer and latest-snapshot state store.

The spine is the canonical normalizer: all adapters push MonitorEvents
into the buffer, and the snapshot store maintains the latest state per entity.
Both are thread-safe via threading.Lock.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Optional

from _loader import (
    MonitorEvent, EventType, SessionState, AgentState, ResourceSnapshot,
)

__all__ = ["EventBuffer", "SnapshotStore", "DEFAULT_BUFFER_CAPACITY"]

DEFAULT_BUFFER_CAPACITY = 10000


class EventBuffer:
    """Bounded append-only circular buffer of MonitorEvents.

    Uses collections.deque(maxlen=N) for O(1) amortized append with
    automatic eviction of oldest entries when full.
    """

    def __init__(self, capacity: int = DEFAULT_BUFFER_CAPACITY):
        self._buf: deque[MonitorEvent] = deque(maxlen=max(1, capacity))
        self._lock = threading.Lock()

    def append(self, event: MonitorEvent) -> None:
        with self._lock:
            self._buf.append(event)

    def snapshot(self, limit: Optional[int] = None) -> list[MonitorEvent]:
        with self._lock:
            if limit is None or limit >= len(self._buf):
                return list(self._buf)
            return list(self._buf)[-limit:]

    def size(self) -> int:
        with self._lock:
            return len(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


class SnapshotStore:
    """Latest state per entity, keyed by (event_type, entity_id).

    Uses hasattr duck-typing for entity ID extraction to avoid
    isinstance failures from class identity mismatches across
    importlib boundaries (now resolved by _loader, but kept as defense).
    """

    def __init__(self):
        self._state: dict[tuple[str, str], tuple[float, object]] = {}
        self._lock = threading.Lock()

    def update(self, event: MonitorEvent) -> None:
        entity_id = self._entity_id(event)
        if entity_id is None:
            return
        key = (event.event_type.value, entity_id)
        with self._lock:
            existing = self._state.get(key)
            if existing and existing[0] > event.timestamp:
                return  # do not overwrite newer state
            self._state[key] = (event.timestamp, event.payload)

    def get(self, event_type: str, entity_id: str) -> Optional[object]:
        with self._lock:
            entry = self._state.get((event_type, entity_id))
            return entry[1] if entry else None

    def all_sessions(self) -> list[SessionState]:
        with self._lock:
            return [
                v[1] for k, v in self._state.items()
                if k[0] == EventType.SESSION_UPDATE.value and hasattr(v[1], "session_id")
            ]

    def all_agents(self) -> list[AgentState]:
        with self._lock:
            return [
                v[1] for k, v in self._state.items()
                if k[0] == EventType.AGENT_UPDATE.value and hasattr(v[1], "agent_id")
            ]

    def latest_resource(self) -> Optional[ResourceSnapshot]:
        with self._lock:
            for k, v in self._state.items():
                if k[0] == EventType.RESOURCE_UPDATE.value and hasattr(v[1], "cpu_percent"):
                    return v[1]
            return None

    def clear(self) -> None:
        with self._lock:
            self._state.clear()

    @staticmethod
    def _entity_id(event: MonitorEvent) -> Optional[str]:
        payload = event.payload
        if hasattr(payload, "session_id"):
            return payload.session_id
        if hasattr(payload, "agent_id"):
            return payload.agent_id
        if hasattr(payload, "cpu_percent"):
            return "_system"
        return None

