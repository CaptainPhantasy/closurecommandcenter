"""
Workflow Task System — async task lifecycle for intelligence streaming.

Harvested from DEEPCODE's workflow_service.py and adapted for the Kernel:
- Task state machine: pending → running → completed | error | cancelled | waiting_for_input
- Multi-client SSE broadcast via asyncio subscriber queues
- Cancel events for long-running tasks
- User-in-the-loop interaction pauses
- Structured activity logging

Unlike DEEPCODE's in-memory-only approach, this persists task metadata to SQLite
so task history survives restarts.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ── Enums ──────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED)


class ActivityType(str, Enum):
    INFO = "info"
    PROGRESS = "progress"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    INTERACTION = "interaction"
    TOKEN = "token"


# ── SSE Event Types ────────────────────────────────────────────────────
# Formal taxonomy derived from DEEPCODE's WS message types + Kernel's needs.

class SSEEvent:
    """Constructs SSE-compatible event dicts."""

    @staticmethod
    def token(content: str) -> dict[str, Any]:
        return {"type": "token", "content": content}

    @staticmethod
    def progress(percent: int, message: str) -> dict[str, Any]:
        return {"type": "progress", "percent": percent, "message": message}

    @staticmethod
    def tool_call(tool: str, args: dict) -> dict[str, Any]:
        return {"type": "tool_call", "tool": tool, "args": args}

    @staticmethod
    def tool_result(tool: str, success: bool, result: str, timing_ms: float = 0) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "tool": tool,
            "success": success,
            "result": result[:4000],  # cap to avoid bloating SSE frames
            "timing_ms": round(timing_ms, 1),
        }

    @staticmethod
    def activity(message: str, category: ActivityType = ActivityType.INFO) -> dict[str, Any]:
        return {"type": "activity", "message": message, "category": category.value}

    @staticmethod
    def interaction(interaction_type: str, title: str, description: str,
                    data: dict | None = None, options: dict | None = None,
                    required: bool = True) -> dict[str, Any]:
        return {
            "type": "interaction",
            "interaction_type": interaction_type,
            "title": title,
            "description": description,
            "data": data or {},
            "options": options or {},
            "required": required,
        }

    @staticmethod
    def complete(result: dict | None = None) -> dict[str, Any]:
        return {"type": "complete", "result": result or {}}

    @staticmethod
    def error(message: str) -> dict[str, Any]:
        return {"type": "error", "error": message}


# ── Data Classes ───────────────────────────────────────────────────────

@dataclass
class ActivityEntry:
    id: str
    timestamp: str  # ISO-8601 UTC
    message: str
    category: ActivityType
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "message": self.message,
            "category": self.category.value,
            "meta": self.meta,
        }


@dataclass
class PendingInteraction:
    """A pending user-in-the-loop interaction."""
    interaction_type: str  # 'requirement_questions' | 'plan_review' | 'confirmation' | etc.
    title: str
    description: str
    data: dict[str, Any] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)  # action → label
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_type": self.interaction_type,
            "title": self.title,
            "description": self.description,
            "data": self.data,
            "options": self.options,
            "required": self.required,
        }


@dataclass
class WorkflowTask:
    """A running or completed intelligence task."""
    task_id: str
    session_id: str
    status: TaskStatus = TaskStatus.PENDING
    model: str = "glm-5.1"
    progress: int = 0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    # Async primitives — not persisted, recreated per-instance
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    # User-in-the-loop
    pending_interaction: PendingInteraction | None = None
    _interaction_response: asyncio.Future[Any] | None = field(default=None, repr=False, init=False)

    # Activity log
    activities: list[ActivityEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "model": self.model,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        if self.pending_interaction:
            d["pending_interaction"] = self.pending_interaction.to_dict()
        if self.result:
            d["result"] = self.result
        return d

    def is_finished(self) -> bool:
        return self.status.is_terminal

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:16]

# ── Activity Persistence ────────────────────────────────────────────

_ACT_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ACT_DB_PATH = _ACT_DB_DIR / "intelligence.db"


def _persist_activity(
    task_id: str,
    session_id: str,
    entry_id: str,
    timestamp: str,
    message: str,
    category: str,
    meta: dict[str, Any],
) -> None:
    """Write an activity log entry to SQLite. Non-blocking — fire-and-forget."""
    try:
        _ACT_DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_ACT_DB_PATH))
        try:
            conn.execute(
                "INSERT INTO activity_log (id, task_id, session_id, message, category, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry_id, task_id, session_id, message, category, json.dumps(meta), datetime.now(timezone.utc).timestamp()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Persistence failure must not break the service
        pass


def load_activities(task_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Load persisted activity entries for a task from SQLite."""
    try:
        conn = sqlite3.connect(str(_ACT_DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, task_id, session_id, message, category, meta, created_at "
                "FROM activity_log WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "task_id": r["task_id"],
                    "session_id": r["session_id"],
                    "message": r["message"],
                    "category": r["category"],
                    "meta": json.loads(r["meta"]) if r["meta"] else {},
                    "timestamp": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception:
        return []

# ── Workflow Service ───────────────────────────────────────────────────

class WorkflowService:
    """
    Manages task lifecycle and multi-client broadcast.

    Each task can have N subscribers (browser tabs, agents, etc.)
    that receive the same SSE events via asyncio.Queue.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, WorkflowTask] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    # ── Task Lifecycle ─────────────────────────────────────────────

    def create_task(self, session_id: str, model: str = "glm-5.1") -> WorkflowTask:
        task_id = _uid()
        task = WorkflowTask(task_id=task_id, session_id=session_id, model=model)
        self._tasks[task_id] = task
        self._subscribers[task_id] = []
        return task

    def get_task(self, task_id: str) -> WorkflowTask | None:
        return self._tasks.get(task_id)

    def start_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.RUNNING
            task.started_at = _now_utc()

    def complete_task(self, task_id: str, result: dict[str, Any] | None = None) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.progress = 100
            task.result = result
            task.completed_at = _now_utc()
            task.cancel_event.set()  # unblock any waiters

    def fail_task(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.ERROR
            task.error = error
            task.completed_at = _now_utc()
            task.cancel_event.set()

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and not task.status.is_terminal:
            task.status = TaskStatus.CANCELLED
            task.cancel_event.set()
            task.completed_at = _now_utc()
            return True
        return False

    # ── Subscription ───────────────────────────────────────────────

    def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        """Create a subscriber queue for SSE events."""
        if task_id not in self._subscribers:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        self._subscribers[task_id].append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if task_id in self._subscribers:
            try:
                self._subscribers[task_id].remove(queue)
            except ValueError:
                pass

    async def broadcast(self, task_id: str, event: dict[str, Any]) -> None:
        """Push an event to all subscribers of a task."""
        subscribers = self._subscribers.get(task_id, [])
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Client is too slow — drop oldest and push
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            except Exception:
                dead.append(q)
        for q in dead:
            self.unsubscribe(task_id, q)

    # ── Activity Logging ───────────────────────────────────────────

    def log_activity(self, task_id: str, message: str,
                     category: ActivityType = ActivityType.INFO,
                     meta: dict[str, Any] | None = None) -> None:
        task = self._tasks.get(task_id)
        if task:
            entry = ActivityEntry(
                id=_uid(),
                timestamp=_now_utc(),
                message=message,
                category=category,
                meta=meta or {},
            )
            task.activities.append(entry)
            # Persist to SQLite (fire-and-forget)
            _persist_activity(
                task_id=task_id,
                session_id=task.session_id,
                entry_id=entry.id,
                timestamp=entry.timestamp,
                message=message,
                category=category.value,
                meta=meta or {},
            )

    # ── User-in-the-Loop ───────────────────────────────────────────

    async def request_interaction(
        self,
        task_id: str,
        interaction_type: str,
        title: str,
        description: str,
        data: dict[str, Any] | None = None,
        options: dict[str, str] | None = None,
        required: bool = True,
    ) -> dict[str, Any]:
        """
        Pause the task and wait for user interaction.

        Broadcasts an 'interaction' event to all subscribers, then
        awaits the user's response. The task status moves to
        WAITING_FOR_INPUT until the response arrives.

        Returns the user's response dict.
        """
        task = self._tasks.get(task_id)
        if not task:
            return {"action": "error", "error": "Task not found"}

        interaction = PendingInteraction(
            interaction_type=interaction_type,
            title=title,
            description=description,
            data=data or {},
            options=options or {},
            required=required,
        )
        task.pending_interaction = interaction
        task.status = TaskStatus.WAITING_FOR_INPUT

        # Create a future to await the response
        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        task._interaction_response = response_future

        # Broadcast interaction event
        event = SSEEvent.interaction(
            interaction_type=interaction_type,
            title=title,
            description=description,
            data=data,
            options=options,
            required=required,
        )
        event["task_id"] = task_id
        await self.broadcast(task_id, event)

        # Wait for response (or cancellation)
        try:
            # Also wait on cancel_event so cancellation unblocks
            done, pending = await asyncio.wait(
                [asyncio.ensure_future(response_future), asyncio.ensure_future(self._wait_cancel(task))],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel the other waiter
            for p in pending:
                p.cancel()

            for d in done:
                try:
                    return d.result()
                except (asyncio.CancelledError, asyncio.InvalidStateError):
                    pass

            return {"action": "cancelled"}
        finally:
            task.pending_interaction = None
            task._interaction_response = None
            if task.status == TaskStatus.WAITING_FOR_INPUT:
                task.status = TaskStatus.RUNNING

    async def _wait_cancel(self, task: WorkflowTask) -> dict[str, Any]:
        """Wait for the cancel event to fire."""
        await task.cancel_event.wait()
        return {"action": "cancelled"}

    def submit_interaction(self, task_id: str, action: str,
                           data: dict[str, Any] | None = None) -> bool:
        """
        Submit a user response to a pending interaction.

        Called from the HTTP endpoint when the user responds.
        Resolves the future that request_interaction is awaiting.
        """
        task = self._tasks.get(task_id)
        if not task or not task._interaction_response:
            return False

        response = {"action": action, "data": data or {}}
        if not task._interaction_response.done():
            task._interaction_response.set_result(response)
        return True

    # ── Cleanup ────────────────────────────────────────────────────

    def cleanup_task(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._subscribers.pop(task_id, None)

    def get_active_tasks(self) -> list[WorkflowTask]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    def get_recent_tasks(self, limit: int = 20) -> list[WorkflowTask]:
        tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.started_at or "",
            reverse=True,
        )
        return tasks[:limit]

    def get_tasks_for_session(self, session_id: str) -> list[WorkflowTask]:
        return [t for t in self._tasks.values() if t.session_id == session_id]


# ── Global Singleton ───────────────────────────────────────────────────

workflow_service = WorkflowService()
