"""
Conversation Store — SQLite-backed message history, tool call logs, session management.

Tables:
  sessions      — conversation sessions with metadata
  messages      — user/assistant/tool messages per session
  tool_calls    — tool invocations with args, results, timing
  checkpoints   — rewind snapshots (full state at a point in time)

The store is the single source of truth for conversation state.
Every message, tool call, and checkpoint flows through here.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DB_PATH = _DB_DIR / "intelligence.db"


def _ensure_db() -> None:
    _DB_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_db()
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
            content     TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            token_count INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

        CREATE TABLE IF NOT EXISTS tool_calls (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            message_id  TEXT REFERENCES messages(id) ON DELETE SET NULL,
            tool_name   TEXT NOT NULL,
            arguments   TEXT NOT NULL DEFAULT '{}',
            result      TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed','blocked')),
            hook_result TEXT,
            started_at  REAL,
            finished_at REAL,
            metadata    TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id, started_at);

        CREATE TABLE IF NOT EXISTS checkpoints (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            label       TEXT NOT NULL DEFAULT '',
            snapshot    TEXT NOT NULL,  -- JSON: full message list + tool state
            created_at  REAL NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id, created_at);

        CREATE TABLE IF NOT EXISTS activity_log (
        	id          TEXT PRIMARY KEY,
        	task_id     TEXT NOT NULL,
        	session_id  TEXT NOT NULL,
        	message     TEXT NOT NULL,
        	category    TEXT NOT NULL DEFAULT 'info',
        	meta        TEXT NOT NULL DEFAULT '{}',
        	created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_task ON activity_log(task_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_activity_session ON activity_log(session_id, created_at);
        	""")
    conn.commit()


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class Message:
    role: str
    content: str
    model: str = ""
    token_count: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "token_count": self.token_count,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    def to_api_message(self) -> dict[str, str]:
        """Format for LLM API messages array (role + content only)."""
        return {"role": self.role, "content": self.content}


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
    message_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    session_id: str = ""
    result: str = ""
    status: str = "pending"
    hook_result: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Store class
# ---------------------------------------------------------------------------

class ConversationStore:
    def __init__(self) -> None:
        self._conn = _connect()
        _init_schema(self._conn)

    # -- Sessions --

    def create_session(self, title: str = "", model: str = "") -> str:
        sid = uuid.uuid4().hex[:16]
        now = time.time()
        self._conn.execute(
            "INSERT INTO sessions (id, title, model, created_at, updated_at) VALUES (?,?,?,?,?)",
            (sid, title, model, now, now),
        )
        self._conn.commit()
        return sid

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # -- Messages --

    def add_message(self, msg: Message) -> Message:
        now = time.time()
        self._conn.execute(
            """INSERT INTO messages (id, session_id, role, content, model, token_count, created_at, metadata)
               VALUES (?,?,?,?,?,?,?,?)""",
            (msg.id, msg.session_id, msg.role, msg.content, msg.model,
             msg.token_count, now, json.dumps(msg.metadata)),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, msg.session_id)
        )
        self._conn.commit()
        msg.created_at = now
        return msg

    def get_messages(self, session_id: str, limit: int = 200) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            Message(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                model=r["model"],
                token_count=r["token_count"],
                created_at=r["created_at"],
                metadata=json.loads(r["metadata"]),
            )
            for r in rows
        ]

    def get_api_messages(self, session_id: str, limit: int = 200) -> list[dict]:
        """Return messages formatted for LLM API consumption."""
        return [m.to_api_message() for m in self.get_messages(session_id, limit)]

    # -- Tool Calls --

    def log_tool_call(self, tc: ToolCall) -> ToolCall:
        self._conn.execute(
            """INSERT INTO tool_calls (id, session_id, message_id, tool_name, arguments, result, status, hook_result, started_at, finished_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tc.id, tc.session_id, tc.message_id, tc.tool_name,
             json.dumps(tc.arguments), tc.result, tc.status, tc.hook_result,
             tc.started_at, tc.finished_at, json.dumps(tc.metadata)),
        )
        self._conn.commit()
        return tc

    def update_tool_call(self, tc_id: str, **kwargs: Any) -> None:
        sets = []
        vals: list[Any] = []
        for k, v in kwargs.items():
            if k == "arguments" and isinstance(v, dict):
                v = json.dumps(v)
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return
        vals.append(tc_id)
        self._conn.execute(
            f"UPDATE tool_calls SET {', '.join(sets)} WHERE id = ?", vals
        )
        self._conn.commit()

    def get_tool_calls(self, session_id: str, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY started_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["arguments"] = json.loads(d["arguments"])
            d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return results

    # -- Stats --

    def get_stats(self) -> dict:
        sessions = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        messages = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        tools = self._conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
        checkpoints = self._conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
        return {
            "sessions": sessions,
            "messages": messages,
            "tool_calls": tools,
            "checkpoints": checkpoints,
        }

    def close(self) -> None:
        self._conn.close()

