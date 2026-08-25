"""
Rewind System — time-travel checkpoint snapshots for conversation state.

Every tool call creates a checkpoint. The full conversation state
(messages + tool call results + session metadata) is snapshotted to SQLite.
Any checkpoint can be restored, rolling back the conversation to that point.

API surface:
  POST /api/rewind/checkpoint  — create named checkpoint
  GET  /api/rewind/list        — list checkpoints for a session
  POST /api/rewind/restore     — restore to a specific checkpoint
  GET  /api/rewind/diff        — show what changed between two checkpoints

Use cases:
  - "That tool call broke things, go back to before it"
  - "Try a different approach from this point"
  - "What did the agent change in the last 5 minutes?"
  - Diff-based review before committing agent changes
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Share the same DB as conversation store
_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DB_PATH = _DB_DIR / "intelligence.db"


def _connect() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@dataclass
class Checkpoint:
    id: str
    session_id: str
    label: str
    snapshot: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "label": self.label,
            "snapshot_size": len(json.dumps(self.snapshot)),
            "created_at": self.created_at,
            "message_count": len(self.snapshot.get("messages", [])),
            "tool_call_count": len(self.snapshot.get("tool_calls", [])),
            "metadata": self.metadata,
        }


class RewindSystem:
    """Create, list, restore, and diff checkpoints."""

    def __init__(self) -> None:
        self._conn = _connect()
        # Schema is created by conversation.py's _init_schema, but ensure
        # checkpoints table exists in case this module is loaded standalone.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                label       TEXT NOT NULL DEFAULT '',
                snapshot    TEXT NOT NULL,
                created_at  REAL NOT NULL,
                metadata    TEXT NOT NULL DEFAULT '{}'
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id, created_at)"
        )
        self._conn.commit()

    # -- Create --

    def checkpoint(
        self,
        session_id: str,
        messages: list[dict],
        tool_calls: list[dict] | None = None,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Checkpoint:
        """Create a checkpoint from current conversation state."""
        cp = Checkpoint(
            id=uuid.uuid4().hex[:16],
            session_id=session_id,
            label=label or f"auto-{int(time.time())}",
            snapshot={
                "messages": messages,
                "tool_calls": tool_calls or [],
                "timestamp": time.time(),
            },
            metadata=metadata or {},
        )
        self._conn.execute(
            "INSERT INTO checkpoints (id, session_id, label, snapshot, created_at, metadata) VALUES (?,?,?,?,?,?)",
            (cp.id, cp.session_id, cp.label, json.dumps(cp.snapshot), cp.created_at, json.dumps(cp.metadata)),
        )
        self._conn.commit()
        return cp

    def auto_checkpoint(self, session_id: str, store: Any) -> Checkpoint | None:
        """Auto-checkpoint using current state from ConversationStore."""
        messages = store.get_api_messages(session_id)
        tool_calls = store.get_tool_calls(session_id)
        if not messages:
            return None
        return self.checkpoint(
            session_id=session_id,
            messages=messages,
            tool_calls=tool_calls,
            label="auto-before-tool",
        )

    # -- Read --

    def list_checkpoints(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        results = []
        for r in rows:
            snap = json.loads(r["snapshot"])
            results.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "label": r["label"],
                "created_at": r["created_at"],
                "message_count": len(snap.get("messages", [])),
                "tool_call_count": len(snap.get("tool_calls", [])),
                "metadata": json.loads(r["metadata"]),
            })
        return results

    def get_checkpoint(self, cp_id: str) -> Checkpoint | None:
        row = self._conn.execute(
            "SELECT * FROM checkpoints WHERE id = ?", (cp_id,)
        ).fetchone()
        if not row:
            return None
        return Checkpoint(
            id=row["id"],
            session_id=row["session_id"],
            label=row["label"],
            snapshot=json.loads(row["snapshot"]),
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]),
        )

    # -- Restore --

    def restore_messages(self, cp_id: str) -> list[dict] | None:
        """Get the message list from a checkpoint (for restoring conversation)."""
        cp = self.get_checkpoint(cp_id)
        if not cp:
            return None
        return cp.snapshot.get("messages", [])

    def restore_tool_calls(self, cp_id: str) -> list[dict] | None:
        """Get the tool call list from a checkpoint."""
        cp = self.get_checkpoint(cp_id)
        if not cp:
            return None
        return cp.snapshot.get("tool_calls", [])

    # -- Diff --

    def diff(self, cp_id_a: str, cp_id_b: str) -> dict[str, Any] | None:
        """Compare two checkpoints. Returns message and tool call diffs."""
        cp_a = self.get_checkpoint(cp_id_a)
        cp_b = self.get_checkpoint(cp_id_b)
        if not cp_a or not cp_b:
            return None

        msgs_a = cp_a.snapshot.get("messages", [])
        msgs_b = cp_b.snapshot.get("messages", [])

        # Messages added/removed between checkpoints
        added_msgs = msgs_b[len(msgs_a):] if len(msgs_b) > len(msgs_a) else []
        removed_msgs = msgs_a[len(msgs_b):] if len(msgs_a) > len(msgs_b) else []

        tools_a = cp_a.snapshot.get("tool_calls", [])
        tools_b = cp_b.snapshot.get("tool_calls", [])

        return {
            "checkpoint_a": {"id": cp_a.id, "label": cp_a.label, "created_at": cp_a.created_at},
            "checkpoint_b": {"id": cp_b.id, "label": cp_b.label, "created_at": cp_b.created_at},
            "messages_added": len(added_msgs),
            "messages_removed": len(removed_msgs),
            "added": added_msgs,
            "removed": removed_msgs,
            "tool_calls_a": len(tools_a),
            "tool_calls_b": len(tools_b),
        }

    # -- Cleanup --

    def delete_checkpoint(self, cp_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM checkpoints WHERE id = ?", (cp_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def prune_session(self, session_id: str, keep: int = 20) -> int:
        """Keep only the N most recent checkpoints for a session."""
        ids = self._conn.execute(
            "SELECT id FROM checkpoints WHERE session_id = ? ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (session_id, keep),
        ).fetchall()
        if not ids:
            return 0
        self._conn.executemany(
            "DELETE FROM checkpoints WHERE id = ?", [(r["id"],) for r in ids]
        )
        self._conn.commit()
        return len(ids)

    def close(self) -> None:
        self._conn.close()

