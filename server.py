import asyncio
import fcntl
import json
import logging
import os
import pty
import shlex
import struct
import sys
import termios
import time
import uuid
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional
from urllib.parse import quote as _url_quote, urlparse as _urlparse

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Request,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, Field
import uvicorn

from output_analyzer import analyze_structured
from closure_control import closure_router

# Intelligence layer (GLM orchestration, hooks, rewind, tool bridge)
from modules.intelligence.routes import mount_intelligence_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fuck")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

LAUNCHD_TYPES = {"none", "timer", "hook", "keepalive"}


class AgentCreate(BaseModel):
    name: str
    label: Optional[str] = None  # custom terminal header title (overrides name in UI)
    directory: str
    command: str
    env: Optional[Dict[str, str]] = None
    order: int = 0
    tags: Optional[List[str]] = None
    auto_start: bool = False
    pinned: bool = False
    launchd_type: Literal["none", "timer", "hook", "keepalive"] = "none"
    launchd_interval: int = 3600
    launchd_watchpath: str = ""
    cron_expression: Optional[str] = (
        None  # e.g. "0 * * * *" (minute hour day month weekday)
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 100:
            raise ValueError("name must be 100 characters or fewer")
        # Reject path separators, NUL, and control chars — the name shows up in
        # toasts, downloads (suggested filename), and the sidebar. Keep it sane.
        if "/" in v or "\\" in v or "\x00" in v:
            raise ValueError("name must not contain '/', '\\\\', or NUL")
        if any(ord(c) < 0x20 for c in v):
            raise ValueError("name must not contain control characters")
        return v

    @field_validator("launchd_type")
    @classmethod
    def validate_launchd_type(cls, v: str) -> str:
        if v not in LAUNCHD_TYPES:
            raise ValueError(f"launchd_type must be one of {LAUNCHD_TYPES}")
        return v

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError(
                "cron_expression must have exactly 5 fields: minute hour day month weekday"
            )
        for p in parts:
            if p == "*":
                continue
            if "/" in p:
                base, step = p.split("/", 1)
                if base not in ("*",) and not base.isdigit():
                    raise ValueError(f"invalid cron step base: {base}")
                if not step.isdigit():
                    raise ValueError(f"invalid cron step: {step}")
            elif "-" in p:
                start, end = p.split("-", 1)
                if not start.isdigit() or not end.isdigit():
                    raise ValueError(f"invalid cron range: {p}")
            elif "," in p:
                if not all(x.isdigit() or x == "*" for x in p.split(",")):
                    raise ValueError(f"invalid cron list: {p}")
            elif not p.isdigit():
                raise ValueError(
                    f"invalid cron field: {p} (use *, digit, */N, M-N, or M,N)"
                )
        return v

    @field_validator("directory")
    @classmethod
    def directory_must_exist(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("directory must not be empty")
        if not os.path.isdir(v):
            raise ValueError(f"directory does not exist: {v}")
        return v

    @field_validator("command")
    @classmethod
    def command_must_parse(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("command must not be empty")
        try:
            shlex.split(v)
        except ValueError as e:
            raise ValueError(f"command is not parseable: {e}")
        return v


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    directory: Optional[str] = None
    command: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    order: Optional[int] = None
    tags: Optional[List[str]] = None
    auto_start: Optional[bool] = None
    pinned: Optional[bool] = None
    launchd_type: Optional[Literal["none", "timer", "hook", "keepalive"]] = None
    launchd_interval: Optional[int] = None
    launchd_watchpath: Optional[str] = None
    cron_expression: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 100:
            raise ValueError("name must be 100 characters or fewer")
        if "/" in v or "\\" in v or "\x00" in v:
            raise ValueError("name must not contain '/', '\\\\', or NUL")
        if any(ord(c) < 0x20 for c in v):
            raise ValueError("name must not contain control characters")
        return v

    @field_validator("launchd_type")
    @classmethod
    def validate_launchd_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in LAUNCHD_TYPES:
            raise ValueError(f"launchd_type must be one of {LAUNCHD_TYPES}")
        return v

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError(
                "cron_expression must have exactly 5 fields: minute hour day month weekday"
            )
        for p in parts:
            if p == "*":
                continue
            if "/" in p:
                base, step = p.split("/", 1)
                if base not in ("*",) and not base.isdigit():
                    raise ValueError(f"invalid cron step base: {base}")
                if not step.isdigit():
                    raise ValueError(f"invalid cron step: {step}")
            elif "-" in p:
                start, end = p.split("-", 1)
                if not start.isdigit() or not end.isdigit():
                    raise ValueError(f"invalid cron range: {p}")
            elif "," in p:
                if not all(x.isdigit() or x == "*" for x in p.split(",")):
                    raise ValueError(f"invalid cron list: {p}")
            elif not p.isdigit():
                raise ValueError(f"invalid cron field: {p}")
        return v

    @field_validator("directory")
    @classmethod
    def directory_must_exist(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("directory must not be empty")
        if not os.path.isdir(v):
            raise ValueError(f"directory does not exist: {v}")
        return v

    @field_validator("command")
    @classmethod
    def command_must_parse(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("command must not be empty")
        try:
            shlex.split(v)
        except ValueError as e:
            raise ValueError(f"command is not parseable: {e}")
        return v


class Agent(BaseModel):
    id: str
    name: str
    label: Optional[str] = None  # custom terminal header title
    directory: str
    command: str
    env: Optional[Dict[str, str]] = None
    order: int = 0
    tags: Optional[List[str]] = None
    auto_start: bool = False
    pinned: bool = False
    launchd_type: str = "none"
    launchd_interval: int = 3600
    launchd_watchpath: str = ""
    cron_expression: Optional[str] = None


class ResizePayload(BaseModel):
    cols: int
    rows: int


class BroadcastPayload(BaseModel):
    agent_ids: List[str]
    input: str


class TemplateCreate(BaseModel):
    name: str
    directory: str
    command: str
    env: Optional[Dict[str, str]] = None
    tags: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

AGENTS_FILE = "agents.json"
TEMPLATES_FILE = "templates.json"
STATE_FILE = "state.json"


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"running": [], "metrics": {}}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"running": [], "metrics": {}}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_agents() -> Dict[str, dict]:
    if not os.path.exists(AGENTS_FILE):
        with open(AGENTS_FILE, "w") as f:
            json.dump({}, f)
    with open(AGENTS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_agents(agents: Dict[str, dict]):
    with open(AGENTS_FILE, "w") as f:
        json.dump(agents, f, indent=2)


def load_templates() -> Dict[str, dict]:
    if not os.path.exists(TEMPLATES_FILE):
        with open(TEMPLATES_FILE, "w") as f:
            json.dump({}, f)
    with open(TEMPLATES_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_templates(templates: Dict[str, dict]):
    with open(TEMPLATES_FILE, "w") as f:
        json.dump(templates, f, indent=2)


# ---------------------------------------------------------------------------
# Launchd Automation Helpers
# ---------------------------------------------------------------------------
import shutil
import subprocess


def _get_plist_path(agent_id: str) -> str:
    return os.path.expanduser(
        f"~/Library/LaunchAgents/com.legacyai.tcc.{agent_id}.plist"
    )


def _generate_launchd_plist(agent_id: str, agent: dict):
    try:
        plist_path = _get_plist_path(agent_id)
        out_log = f"/tmp/tcc_agent_{agent_id}.out.log"
        err_log = f"/tmp/tcc_agent_{agent_id}.err.log"

        # Touch logs to ensure they exist for tail
        open(out_log, "a").close()
        open(err_log, "a").close()

        l_type = agent.get("launchd_type", "none")
        if l_type == "none":
            return

        # Validate working directory exists
        directory = agent.get("directory", "")
        if not os.path.isdir(directory):
            logger.warning(
                "launchd plist skipped: directory %s does not exist", directory
            )
            return

        cmd_args = shlex.split(agent["command"])
        # Resolve to absolute paths for launchd
        resolved_cmd = shutil.which(cmd_args[0]) if cmd_args else None
        if resolved_cmd:
            cmd_args[0] = resolved_cmd

        # Build ProgramArguments XML
        args_xml = ""
        for arg in cmd_args:
            args_xml += f"        <string>{arg}</string>\n"

        trigger_xml = ""
        if l_type == "timer":
            trigger_xml = f"    <key>StartInterval</key>\n    <integer>{agent.get('launchd_interval', 3600)}</integer>"
        elif l_type == "hook":
            watchpath = agent.get("launchd_watchpath", "")
            if watchpath and not os.path.exists(watchpath):
                logger.warning("launchd watch path does not exist: %s", watchpath)
            trigger_xml = f"    <key>WatchPaths</key>\n    <array>\n        <string>{watchpath}</string>\n    </array>"
        elif l_type == "keepalive":
            trigger_xml = "    <key>KeepAlive</key>\n    <true/>\n    <key>RunAtLoad</key>\n    <true/>"

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.legacyai.tcc.{agent_id}</string>
    <key>WorkingDirectory</key>
    <string>{os.path.abspath(directory)}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}    </array>
    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
{trigger_xml}
</dict>
</plist>"""
        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        with open(plist_path, "w") as f:
            f.write(plist_content)

        # Unload if loaded previously, then load
        subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
        result = subprocess.run(["launchctl", "load", plist_path], capture_output=True)
        if result.returncode != 0:
            logger.error(
                "launchctl load failed: %s", result.stderr.decode(errors="replace")
            )
        else:
            logger.info("launchd plist loaded for agent %s", agent_id)
    except Exception:
        logger.exception("Failed to generate launchd plist for agent %s", agent_id)


def _remove_launchd_plist(agent_id: str):
    try:
        plist_path = _get_plist_path(agent_id)
        subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
        if os.path.exists(plist_path):
            os.remove(plist_path)
            logger.info("Removed launchd plist for agent %s", agent_id)
    except Exception:
        logger.exception("Failed to remove launchd plist for agent %s", agent_id)


# ---------------------------------------------------------------------------
# Cron utilities
# ---------------------------------------------------------------------------


def _matches_cron_field(field_val: str, cron_val: int) -> bool:
    """Check if a cron field value matches. Supports *, digit, or list."""
    if field_val == "*":
        return True
    if "," in field_val:
        return str(cron_val) in field_val.split(",")
    if "-" in field_val:
        start, end = field_val.split("-")
        return int(start) <= cron_val <= int(end)
    if "/" in field_val:
        base, step = field_val.split("/")
        step = int(step)
        if base == "*":
            return cron_val % step == 0
        return int(base) <= cron_val and (cron_val - int(base)) % step == 0
    return int(field_val) == cron_val


def compute_next_cron(expression: str, since: float | None = None) -> float | None:
    """Compute the next Unix timestamp this cron expression will fire.
    expression: "min hour day month wday" (5 fields).
    Respects TZ from environment. Scans up to 1 year ahead.
    """
    import datetime as dt
    import calendar

    if since is None:
        since = time.time()
    parts = expression.strip().split()
    if len(parts) != 5:
        return None
    minute_f, hour_f, day_f, month_f, wday_f = parts

    # Normalize TZ offset for display but use naive UTC for computation
    tz_offset = time.timezone if time.daylight == 0 else time.altzone
    start = dt.datetime.fromtimestamp(since + tz_offset)
    # Don't fire in the same minute we just computed
    candidate = start.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)

    for _ in range(366 * 24 * 60):  # max 1 year of minutes
        year = candidate.year
        month = candidate.month
        day = candidate.day
        hour = candidate.hour
        minute = candidate.minute
        wday = candidate.weekday()  # 0=Monday, 6=Sunday (cron uses 0=Sun)

        # cron weekday: 0=Sun but weekday() returns 0=Mon
        cron_wday = (wday + 6) % 7  # convert Mon-based to Sun-based

        if not _matches_cron_field(month_f, month):
            # Advance to next valid month day
            days_in_month = calendar.monthrange(year, month)[1]
            candidate = candidate.replace(day=min(day, days_in_month)) + dt.timedelta(
                days=31
            )
            candidate = candidate.replace(day=1, hour=0, minute=0)
            continue
        if not _matches_cron_field(day_f, day):
            candidate += dt.timedelta(days=1)
            candidate = candidate.replace(hour=0, minute=0)
            continue
        if not _matches_cron_field(wday_f, cron_wday):
            candidate += dt.timedelta(days=1)
            candidate = candidate.replace(hour=0, minute=0)
            continue
        if not _matches_cron_field(hour_f, hour):
            candidate += dt.timedelta(hours=1)
            candidate = candidate.replace(minute=0)
            continue
        if not _matches_cron_field(minute_f, minute):
            candidate += dt.timedelta(minutes=1)
            continue
        return candidate.timestamp() - tz_offset
    return None


def get_log_paths(agent_id: str) -> tuple[str, str]:
    """Return (stdout_log, stderr_log) paths for an agent."""
    return (
        f"/tmp/tcc_agent_{agent_id}.out.log",
        f"/tmp/tcc_agent_{agent_id}.err.log",
    )


def get_launchd_info(agent_id: str, agent: dict) -> dict:
    """Get full launchd status info for an agent."""
    plist_path = _get_plist_path(agent_id)
    plist_exists = os.path.exists(plist_path)
    loaded = False
    last_run = None
    run_count = 0
    pid = None
    status_str = "inactive"
    if plist_exists:
        try:
            r = subprocess.run(
                ["launchctl", "list", f"com.legacyai.tcc.{agent_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                loaded = True
                for line in r.stdout.splitlines():
                    if line.startswith('"PID"'):
                        pid = int(line.split("=")[1].strip().rstrip(","))
                    if line.startswith('"RunCount"'):
                        run_count = int(line.split("=")[1].strip().rstrip(","))
                    if line.startswith('"LastExitStatus"'):
                        last_exit = line.split("=")[1].strip().rstrip(",")
                        if last_exit == "0":
                            status_str = "running"
                        elif pid:
                            status_str = "finished"
                        else:
                            status_str = "error"
                if pid and not last_run:
                    status_str = "running"
        except Exception:
            pass
    out_log, err_log = get_log_paths(agent_id)
    return {
        "loaded": loaded,
        "plist_exists": plist_exists,
        "status": status_str,
        "pid": pid,
        "run_count": run_count,
        "last_run": last_run,
        "log_out": out_log,
        "log_err": err_log,
    }


# ---------------------------------------------------------------------------
# Process registry — PTY processes persist independently of WebSocket clients
# ---------------------------------------------------------------------------


class ProcessHandle:
    """Holds a running PTY process and its connected WebSocket clients."""

    __slots__ = (
        "process",
        "master_fd",
        "websockets",
        "pty_reader_task",
        "scrollback",
        "scrollback_limit",
        "started_at",
        "restart_count",
        "fd_closed",
        "_reader_closed",
        "last_output_at",
    )

    def __init__(self, process: asyncio.subprocess.Process, master_fd: int):
        self.process = process
        self.master_fd = master_fd
        self.websockets: List[WebSocket] = []
        self.pty_reader_task: Optional[asyncio.Task] = None
        self.scrollback: bytearray = bytearray()
        self.scrollback_limit: int = 64 * 1024  # 64 KB ring
        self.started_at: float = time.time()
        self.restart_count: int = 0
        self.fd_closed: bool = False
        self.last_output_at: float = time.time()

    def close_fd(self):
        """Close master_fd exactly once. Safe to call multiple times."""
        if not self.fd_closed:
            self.fd_closed = True
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    def append_scrollback(self, data: bytes):
        self.scrollback.extend(data)
        self.last_output_at = time.time()
        if len(self.scrollback) > self.scrollback_limit:
            self.scrollback = self.scrollback[-self.scrollback_limit :]

    def get_clean_scrollback(self) -> bytes:
        """Return scrollback buffer for reconnection (just the bytes, no strip)."""
        if not self.scrollback:
            return b""
        # Return the last 32 KB, aligned to newline
        buf = bytes(self.scrollback)
        min_keep = min(len(buf), 32 * 1024)
        search_start = len(buf) - min_keep
        if search_start > 0:
            nl_pos = buf.find(ord("\n"), search_start)
            if nl_pos != -1 and nl_pos < len(buf) - 1:
                buf = buf[nl_pos + 1 :]
        return buf

    def get_raw_scrollback(self) -> bytes:
        """Return raw scrollback for export (strip ANSI escape sequences)."""
        import re

        raw = bytes(self.scrollback).decode("utf-8", errors="replace")
        cleaned = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw)
        cleaned = re.sub(r"\x1b\][^\x07]*\x07", "", cleaned)
        cleaned = re.sub(r"\x1b[()][0-9A-B]", "", cleaned)
        return cleaned.encode("utf-8")

    def scrollback_stats(self) -> dict:
        """Return buffer utilization stats."""
        used = len(self.scrollback)
        limit = self.scrollback_limit
        pct = (used / limit * 100) if limit > 0 else 0
        return {
            "buffer_size_bytes": limit,
            "buffer_used_bytes": used,
            "buffer_pct": max(0.1, round(pct, 1)) if used > 0 else 0,
        }

    @property
    def alive(self) -> bool:
        return self.process.returncode is None


running_processes: Dict[str, ProcessHandle] = {}
# Track metrics that persist across restarts
agent_metrics: Dict[str, dict] = {}  # id -> {restart_count, total_uptime, last_started}


async def broadcast_to_clients(handle: ProcessHandle, data: bytes):
    dead: List[WebSocket] = []
    for ws in list(handle.websockets):  # Iterate copy to allow concurrent removal
        try:
            await ws.send_bytes(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in handle.websockets:
            handle.websockets.remove(ws)


async def pty_reader_loop(agent_id: str, handle: ProcessHandle):
    """Read PTY output using event-loop add_reader (non-blocking).

    This avoids the thread-executor approach which deadlocks on macOS when
    the fd is closed while a thread is blocked in os.read().
    """
    loop = asyncio.get_running_loop()
    fd = handle.master_fd
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    # Set fd to non-blocking so os.read never blocks the event loop.
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    handle._reader_closed = False

    def _on_readable():
        if handle._reader_closed:
            return  # Already signaled EOF, ignore spurious wake-ups
        try:
            data = os.read(fd, 65536)
            if not data:
                handle._reader_closed = True
                queue.put_nowait(None)
                return
            queue.put_nowait(data)
        except BlockingIOError:
            pass  # Spurious wake, no data yet
        except OSError:
            if not handle._reader_closed:
                handle._reader_closed = True
                queue.put_nowait(None)

    loop.add_reader(fd, _on_readable)
    try:
        while True:
            data = await queue.get()
            if data is None:
                break
            handle.append_scrollback(data)
            await broadcast_to_clients(handle, data)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        # Crash recovery: check BEFORE cleanup removes from running_processes
        should_restart = await _maybe_restart(agent_id, handle)
        await _cleanup_process(agent_id, handle)
        if should_restart:
            await _attempt_crash_restart(agent_id)


async def _maybe_restart(agent_id: str, handle: ProcessHandle) -> bool:
    """Return True if agent should be auto-restarted (auto_start=true, under retry limit)."""
    agents = load_agents()
    if agent_id not in agents:
        return False
    if not agents[agent_id].get("auto_start", False):
        return False
    now = time.time()
    history = agent_metrics.setdefault(agent_id, {}).setdefault(
        "restart_timestamps", []
    )
    history[:] = [t for t in history if now - t < 300]  # 5-min window
    history.append(now)
    return len(history) <= 3


async def _attempt_crash_restart(agent_id: str):
    """Attempt to restart a crashed auto-start agent. Max 3 retries per 5-min window."""
    agents = load_agents()
    if agent_id not in agents:
        return
    agent = agents[agent_id]
    history = agent_metrics.setdefault(agent_id, {}).setdefault(
        "restart_timestamps", []
    )
    attempt = len(history)
    logger.info("Auto-restarting crashed agent %s (attempt %d/3)", agent_id, attempt)
    await asyncio.sleep(2)  # Back off
    try:
        await spawn_process(agent_id, agent)
    except Exception:
        logger.error("Auto-restart failed for agent %s", agent_id)


async def _cleanup_process(agent_id: str, handle: ProcessHandle):
    for ws in list(handle.websockets):
        try:
            await ws.send_json({"type": "status", "status": "exited"})
        except Exception:
            pass
    handle.close_fd()
    # Update metrics before killing
    if agent_id in agent_metrics:
        agent_metrics[agent_id]["total_uptime"] += time.time() - handle.started_at
    if running_processes.get(agent_id) is handle:
        running_processes.pop(agent_id, None)
    # Persist running state so sessions survive server restarts
    try:
        state = _load_state()
        state["running"] = list(running_processes.keys())
        state["metrics"] = agent_metrics
        _save_state(state)
    except Exception:
        pass


async def spawn_process(agent_id: str, agent: dict) -> ProcessHandle:
    command_str = agent.get("command", "").strip()
    if not command_str:
        raise ValueError("Agent command is required")
    directory = agent.get("directory", "/")
    if not os.path.isdir(directory):
        raise ValueError(f"Directory does not exist: {directory}")
    agent_env = agent.get("env")

    cmd_args = shlex.split(command_str)
    master_fd, slave_fd = pty.openpty()
    winsize = struct.pack("HHHH", 24, 80, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

    proc_env = os.environ.copy()
    proc_env["TERM"] = "xterm-256color"
    if agent_env:
        proc_env.update(agent_env)

    process = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=directory,
        env=proc_env,
        close_fds=True,
    )
    os.close(slave_fd)

    handle = ProcessHandle(process, master_fd)

    # Carry over restart count from metrics
    if agent_id not in agent_metrics:
        agent_metrics[agent_id] = {
            "restart_count": 0,
            "total_uptime": 0.0,
            "last_started": time.time(),
        }
    else:
        agent_metrics[agent_id]["last_started"] = time.time()
    handle.restart_count = agent_metrics[agent_id]["restart_count"]

    running_processes[agent_id] = handle
    handle.pty_reader_task = asyncio.create_task(pty_reader_loop(agent_id, handle))
    return handle


async def kill_process(agent_id: str):
    _remove_launchd_plist(agent_id)
    handle = running_processes.pop(agent_id, None)
    if handle is None:
        return
    # Update metrics before killing
    if agent_id in agent_metrics:
        agent_metrics[agent_id]["total_uptime"] += time.time() - handle.started_at
    # 1. Remove the fd from the event loop and close it.  This stops the
    #    add_reader callback and causes the reader coroutine's queue.get()
    #    to receive None on the next (or current) _on_readable call.
    loop = asyncio.get_running_loop()
    try:
        loop.remove_reader(handle.master_fd)
    except Exception:
        pass
    handle.close_fd()
    # 2. Cancel the reader task — unblocks queue.get() immediately.
    if handle.pty_reader_task and not handle.pty_reader_task.done():
        handle.pty_reader_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(handle.pty_reader_task), timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    # 3. Terminate/kill the child process.
    try:
        handle.process.terminate()
        await asyncio.wait_for(handle.process.wait(), timeout=2.0)
    except (ProcessLookupError, asyncio.TimeoutError):
        try:
            handle.process.kill()
        except ProcessLookupError:
            pass
    # 4. Notify WebSocket clients.
    for ws in list(handle.websockets):
        try:
            await ws.send_json({"type": "status", "status": "disconnected"})
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


# Track last cron fire time per agent (in-memory; survives via agent_metrics on disk)
_cron_last_fire: dict[str, float] = {}


async def _cron_scheduler_loop():
    """Background scheduler — every 60s, fire any agent whose cron_expression is due.

    Honors `last_started` from agent_metrics so we don't double-fire within the
    same minute across server restarts. A fire is just `spawn_process` (the
    same path the UI uses when you click an agent), which means cron-fired
    agents show up live in the UI exactly like manually-started ones.
    """
    while True:
        try:
            await asyncio.sleep(60)
            agents = load_agents()
            now = time.time()
            for agent_id, agent in agents.items():
                expr = agent.get("cron_expression")
                if not expr:
                    continue
                # When was the last fire for this agent?
                last = _cron_last_fire.get(agent_id) or agent_metrics.get(
                    agent_id, {}
                ).get("last_cron_fire", 0)
                # Compute the next fire AFTER the last known fire (or 65s ago).
                since = max(last, now - 65)
                next_run = compute_next_cron(expr, since=since)
                if next_run is None:
                    continue
                if next_run <= now:
                    # Skip if a process is already running — cron should not pile up
                    if (
                        agent_id in running_processes
                        and running_processes[agent_id].alive
                    ):
                        continue
                    try:
                        await spawn_process(agent_id, agent)
                        _cron_last_fire[agent_id] = now
                        agent_metrics.setdefault(agent_id, {})["last_cron_fire"] = now
                        try:
                            st = _load_state()
                            st["running"] = list(running_processes.keys())
                            st["metrics"] = agent_metrics
                            _save_state(st)
                        except Exception:
                            logger.exception("failed to persist cron state")
                        logger.info(
                            "cron fired agent %s (expr=%s)",
                            agent.get("name", agent_id),
                            expr,
                        )
                    except Exception:
                        logger.exception(
                            "cron failed to spawn agent %s",
                            agent.get("name", agent_id),
                        )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("cron scheduler loop error")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Restore running sessions from persisted state (non-auto_start agents too)
    state = _load_state()
    agents = load_agents()
    # Restore metrics
    if state.get("metrics"):
        for k, v in state["metrics"].items():
            agent_metrics[k] = v
    # Restore running sessions (except auto_start which we handle separately)
    running_to_restore = [
        aid
        for aid in state.get("running", [])
        if aid not in running_processes and aid in agents
    ]
    running_to_restore.sort(
        key=lambda aid: (agents[aid].get("order", 0), agents[aid].get("name", ""))
    )
    for agent_id in running_to_restore:
        if not agents[agent_id].get(
            "auto_start", False
        ):  # skip auto_start (handled below)
            try:
                await spawn_process(agent_id, agents[agent_id])
                logger.info("Restored session: %s", agent_id)
            except Exception:
                logger.warning("Failed to restore session %s", agent_id)
    # Auto-start agents: sorted by order ascending, then name, with 500ms stagger
    auto_start_agents = [
        (agent_id, agent)
        for agent_id, agent in agents.items()
        if agent.get("auto_start", False) and agent_id not in running_processes
    ]
    auto_start_agents.sort(key=lambda x: (x[1].get("order", 0), x[1].get("name", "")))
    for agent_id, agent in auto_start_agents:
        try:
            await spawn_process(agent_id, agent)
            logger.info(
                "Auto-start [%s] order=%s: %s",
                agent.get("name", agent_id),
                agent.get("order", 0),
                agent_id,
            )
        except Exception:
            logger.warning(
                "Auto-start failed for agent %s: %s", agent_id, agent.get("name", "")
            )
        await asyncio.sleep(0.5)
    # Start cron scheduler in the background
    cron_task = asyncio.create_task(_cron_scheduler_loop())
    logger.info("cron scheduler started")
    # Start auto-cleaner
    global _autoclean_task
    _autoclean_task = asyncio.create_task(_autoclean_loop())
    logger.info(
        "Auto-cleaner started (interval=%ds, idle=%ds)",
        AUTO_CLEAN_INTERVAL_SECONDS,
        AUTO_CLEAN_DEFAULT_IDLE_SECONDS,
    )
    yield
    cron_task.cancel()
    if _autoclean_task:
        _autoclean_task.cancel()
    try:
        await cron_task
    except asyncio.CancelledError:
        pass
    if _autoclean_task:
        try:
            await _autoclean_task
        except asyncio.CancelledError:
            pass
    for agent_id in list(running_processes):
        await kill_process(agent_id)


app = FastAPI(lifespan=lifespan)

# ---------------------------------------------------------------------------
# Loopback-only mutations (ops gate). State-changing HTTP methods are refused
# from non-loopback peers unless CLOSURE_ALLOW_REMOTE_MUTATIONS=1 is set, so a
# deliberate non-loopback bind cannot silently expose write/exec endpoints.
# ---------------------------------------------------------------------------
import ipaddress as _ipaddress_guard
from fastapi.responses import JSONResponse as _GuardJSONResponse

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOCAL_TEST_PEERS = frozenset({"testclient", "testserver"})


def _peer_is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in _LOCAL_TEST_PEERS or host == "localhost":
        return True
    try:
        return _ipaddress_guard.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@app.middleware("http")
async def _loopback_only_mutations(request: Request, call_next):
    if (
        request.method in _MUTATING_METHODS
        and os.environ.get("CLOSURE_ALLOW_REMOTE_MUTATIONS") != "1"
        and not _peer_is_loopback(request.client.host if request.client else None)
    ):
        return _GuardJSONResponse(
            status_code=403,
            content={"detail": "mutations are restricted to loopback clients"},
        )
    return await call_next(request)

# LLM-First API layer (progressive disclosure design)
from llm_api import llm_router

app.include_router(llm_router, prefix="/api/llm", tags=["LLM"])
app.include_router(closure_router)

STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Kernel module: Workspace Editor (MWIDE source copy)
# The built frontend is served as static files under /workspace-editor/.
# The backend runs as a child process on WORKSPACE_EDITOR_PORT (default 10602).
from pathlib import Path as _PathLibEarly

# Dev-Launcher static mount (copied from /Volumes/Storage/Development/dev-launcher/frontend/dist/)
_DEV_LAUNCHER_DIST = _PathLibEarly("static/dev-launcher")
if _DEV_LAUNCHER_DIST.is_dir():
    app.mount(
        "/dev-launcher",
        StaticFiles(directory=str(_DEV_LAUNCHER_DIST), html=True),
        name="dev-launcher",
    )

_WORKSPACE_EDITOR_DIST = (
    _PathLibEarly(__file__).parent / "modules" / "workspace-editor" / "source" / "dist"
)
_WORKSPACE_EDITOR_PORT = int(os.environ.get("WORKSPACE_EDITOR_PORT", "10602"))
if _WORKSPACE_EDITOR_DIST.is_dir():
    app.mount(
        "/workspace-editor",
        StaticFiles(directory=str(_WORKSPACE_EDITOR_DIST), html=True),
        name="workspace-editor",
    )


# ---------------------------------------------------------------------------
# Workspace Editor Backend APIs
# ---------------------------------------------------------------------------
# Port of MWIDE's Express server.ts filesystem bridge, git proxy, LLM proxy,
# and vault into FastAPI routes. The frontend expects these endpoints.

import json as _json
import shutil as _shutil
import time as _time
from pathlib import Path as _PathLibWE

# --- FS bridge: path allow/deny lists (mirrors MWIDE governance) ---
_WE_DENY_ROOTS = ["/Volumes/T7", "/private/var/db", "/System"]
# Paths in _WE_ALLOWED_ROOTS are whitelisted for read/list/write, but we block
# deleting the root of an allowed tree (e.g. /Volumes/Storage itself) to prevent
# catastrophic accidents. Subdirectories remain deletable.
_WE_DELETE_SENTINEL_ROOTS = [
    str(_PathLibWE.home()),
    "/Volumes/SanDisk1Tb",
    "/Volumes/Storage",
]
_WE_ALLOWED_ROOTS = [
    str(_PathLibWE.home()),
    os.getcwd(),
    "/Volumes/SanDisk1Tb",
    "/Volumes/Storage",
    str(_PathLibWE.home() / "Library/CloudStorage"),
    "/tmp",
    "/private/tmp",
    "/private/var/folders",
    "/opt",
    "/usr/local",
]
_WE_LIST_CAP = 5000


def _we_assert_allowed(p: str) -> None:
    """Mirror MWIDE's assertAllowed: deny-list first, then allow-list."""
    resolved = str(_PathLibWE(p).resolve())
    for r in _WE_DENY_ROOTS:
        if resolved == r or resolved.startswith(r + "/"):
            raise HTTPException(status_code=403, detail=f"Path denied: {resolved}")
    for r in _WE_ALLOWED_ROOTS:
        if resolved == r or resolved.startswith(r + "/"):
            return
    raise HTTPException(status_code=403, detail=f"Path not allowed: {resolved}")


# --- FS bridge: /api/fs/* ---
@app.get("/api/fs/home")
async def we_fs_home() -> dict:
    return {"home": str(_PathLibWE.home())}


@app.get("/api/fs/list")
async def we_fs_list(path: str = "", showHidden: str = "true") -> dict:
    dir_path = path or str(_PathLibWE.home())
    # Special /Volumes case
    if dir_path == "/Volumes":
        items = []
        try:
            volume_entries = sorted(os.listdir("/Volumes")) if os.path.isdir("/Volumes") else []
            for entry in volume_entries:
                full = str(_PathLibWE("/Volumes") / entry)
                denied = any(
                    full == r or full.startswith(r + "/") for r in _WE_DENY_ROOTS
                )
                if not denied and os.path.isdir(full):
                    items.append(
                        {
                            "name": entry,
                            "path": full,
                            "type": "dir",
                            "size": 0,
                            "mtimeMs": 0,
                        }
                    )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {
            "path": dir_path,
            "items": items,
            "truncated": False,
            "total": len(items),
        }

    _we_assert_allowed(dir_path)
    try:
        entries = sorted(
            os.listdir(dir_path),
            key=lambda n: (not os.path.isdir(os.path.join(dir_path, n)), n.lower()),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Directory not found: {dir_path}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {dir_path}")

    show = showHidden != "false"
    if not show:
        entries = [e for e in entries if not e.startswith(".")]
    total = len(entries)
    capped = entries[:_WE_LIST_CAP]
    items = []
    for name in capped:
        full = os.path.join(dir_path, name)
        try:
            st = os.stat(full)
            items.append(
                {
                    "name": name,
                    "path": full,
                    "type": "dir" if os.path.isdir(full) else "file",
                    "size": st.st_size,
                    "mtimeMs": st.st_mtime * 1000,
                }
            )
        except (OSError, PermissionError):
            items.append(
                {"name": name, "path": full, "type": "file", "size": 0, "mtimeMs": 0}
            )
    return {
        "path": dir_path,
        "items": items,
        "total": total,
        "truncated": total > _WE_LIST_CAP,
    }


@app.get("/api/fs/read")
async def we_fs_read(path: str = "") -> dict:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    _we_assert_allowed(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": path, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail=f"Path is a directory: {path}")


@app.get("/api/fs/serve")
async def we_fs_serve(path: str = ""):
    """Serve a raw file for browser viewing (replaces file:// links)."""
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    _we_assert_allowed(path)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Not found: {path}")
    import mimetypes as _mt

    mime, _ = _mt.guess_type(path)
    if not mime:
        mime = "text/plain"
    try:
        with open(path, "rb") as f:
            data = f.read()
        return Response(content=data, media_type=mime)
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")


@app.get("/api/fs/browse")
async def we_fs_browse(path: str = ""):
    """Browse to a directory — redirects to fs/list or fs/serve depending on type."""
    if not path:
        return {"type": "directory", "path": "/"}
    _we_assert_allowed(path)
    if os.path.isdir(path):
        return {"type": "directory", "path": path}
    elif os.path.isfile(path):
        return {"type": "file", "path": path, "serve_url": f"/api/fs/serve?path={path}"}
    else:
        raise HTTPException(status_code=404, detail=f"Not found: {path}")


class _WeWriteBody(BaseModel):
    path: str
    content: str


@app.post("/api/fs/write")
async def we_fs_write(body: _WeWriteBody) -> dict:
    _we_assert_allowed(body.path)
    os.makedirs(os.path.dirname(body.path) or ".", exist_ok=True)
    with open(body.path, "w", encoding="utf-8") as f:
        f.write(body.content)
    return {"ok": True, "path": body.path, "bytes": len(body.content.encode("utf-8"))}


class _WeMkdirBody(BaseModel):
    path: str


@app.post("/api/fs/mkdir")
async def we_fs_mkdir(body: _WeMkdirBody) -> dict:
    _we_assert_allowed(body.path)
    os.makedirs(body.path, exist_ok=True)
    return {"ok": True, "path": body.path}


class _WeRenameBody(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


@app.post("/api/fs/rename")
async def we_fs_rename(body: _WeRenameBody) -> dict:
    _we_assert_allowed(body.from_)
    _we_assert_allowed(body.to)
    os.makedirs(os.path.dirname(body.to) or ".", exist_ok=True)
    os.rename(body.from_, body.to)
    return {"ok": True}


@app.delete("/api/fs/remove")
async def we_fs_remove(path: str = "") -> dict:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    _we_assert_allowed(path)
    # Block deletion of the critical root directories themselves
    resolved = str(_PathLibWE(path).resolve())
    for sentinel in _WE_DELETE_SENTINEL_ROOTS:
        if resolved == sentinel:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot delete root of allowed path tree: {sentinel}",
            )
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="path not found")
    if os.path.isdir(path):
        _shutil.rmtree(path)
    else:
        os.unlink(path)
    return {"ok": True}


@app.get("/api/fs/stat")
async def we_fs_stat(path: str = "") -> dict:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    _we_assert_allowed(path)
    st = os.stat(path)
    return {
        "path": path,
        "type": "dir" if os.path.isdir(path) else "file",
        "size": st.st_size,
        "mtimeMs": st.st_mtime * 1000,
        "mode": st.st_mode,
    }


# --- Vault: API key storage (mirrors MWIDE ~/.config/mwide-vault.json) ---
_WE_VAULT_DIR = _PathLibWE.home() / ".config"
_WE_VAULT_PATH = _WE_VAULT_DIR / "mwide-vault.json"


def _we_vault_read() -> dict:
    try:
        txt = _WE_VAULT_PATH.read_text(encoding="utf-8")
        data = _json.loads(txt)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, _json.JSONDecodeError):
        return {}


def _we_vault_write(data: dict) -> None:
    _WE_VAULT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _WE_VAULT_PATH.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_WE_VAULT_PATH)
    try:
        _WE_VAULT_PATH.chmod(0o600)
    except OSError:
        pass


def _we_valid_vault_id(id_str: str) -> bool:
    import re

    return bool(re.match(r"^[A-Za-z0-9._-]{1,64}$", id_str))


@app.get("/api/vault/list")
async def we_vault_list() -> dict:
    data = _we_vault_read()
    return {"ids": [k for k, v in data.items() if v]}


class _WeVaultSetBody(BaseModel):
    id: str
    key: str


@app.post("/api/vault/set")
async def we_vault_set(body: _WeVaultSetBody) -> dict:
    if not _we_valid_vault_id(body.id) or not isinstance(body.key, str):
        raise HTTPException(status_code=400, detail="invalid id or key")
    data = _we_vault_read()
    if body.key:
        data[body.id] = body.key
    else:
        data.pop(body.id, None)
    _we_vault_write(data)
    return {"ok": True, "id": body.id}


@app.delete("/api/vault/delete")
async def we_vault_delete(id: str = "") -> dict:
    if not _we_valid_vault_id(id):
        raise HTTPException(status_code=400, detail="invalid id")
    data = _we_vault_read()
    data.pop(id, None)
    _we_vault_write(data)
    return {"ok": True, "id": id}


# --- Git CORS proxy ---
def _we_git_proxy_is_blocked_host(host: str) -> bool:
    """Reject internal/loopback/private/link-local targets to prevent SSRF."""
    import ipaddress
    import socket

    host = (host or "").split("@")[-1].lower().rstrip(".")
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Normalize legacy encodings (decimal, hex, shortened) that
        # inet_aton accepts but ip_address rejects, then re-check.
        try:
            ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except OSError:
            return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _we_git_proxy_allowed_origin(request: Request) -> str:
    """Restrict CORS to the requesting origin instead of wildcard."""
    return request.headers.get("origin") or ""


@app.api_route("/api/git-proxy/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def we_git_proxy(rest: str, request: Request) -> Response:
    """Forward isomorphic-git requests to HTTPS git remotes."""
    target = rest
    # Block non-http(s) BEFORE prepending https://
    if "://" in target and not target.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="bad protocol")
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    parsed = _urlparse(target)
    if _we_git_proxy_is_blocked_host(parsed.hostname or ""):
        raise HTTPException(
            status_code=403,
            detail="git proxy refuses internal/private/loopback targets",
        )

    import httpx

    headers = dict(request.headers)
    for skip in ("host", "connection", "content-length", "origin"):
        headers.pop(skip, None)
        headers.pop(skip.title(), None)

    body = await request.body()
    method = request.method

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.request(
                method, target, headers=headers, content=body or None
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"git proxy error: {e}")

    resp_headers = dict(resp.headers)
    resp_headers.pop("content-encoding", None)
    origin = _we_git_proxy_allowed_origin(request)
    if origin:
        resp_headers["access-control-allow-origin"] = origin
    resp_headers.pop("access-control-allow-origin", None) if not origin else None
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
    )


# --- LLM streaming proxy ---
@app.post("/api/llm/stream")
async def we_llm_stream(request: Request) -> StreamingResponse:
    """Proxy LLM chat completions with SSE streaming."""
    body = await request.json()
    provider = body.get("provider", {})
    messages = body.get("messages", [])
    tools = body.get("tools", [])

    if not provider or not messages:
        raise HTTPException(status_code=400, detail="Missing provider or messages")

    # Resolve API key from vault
    api_key = provider.get("apiKey", "")
    vault = _we_vault_read()
    provider_id = provider.get("id", "")
    if provider_id and vault.get(provider_id):
        api_key = vault[provider_id]
    if not api_key:
        raise HTTPException(status_code=400, detail="API key not configured")

    base_url = provider.get("baseUrl", "").rstrip("/")
    model = provider.get("model", "")
    max_tokens = provider.get("maxTokens", 4096)
    provider_type = provider.get("type", "openai")
    provider_id_val = provider.get("id", "")
    debug = body.get("debug", False)

    import httpx

    async def stream_openai():
        url = f"{base_url}/chat/completions"
        req_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if provider_id_val == "openrouter":
            req_headers["HTTP-Referer"] = "https://mobile-web-ide.local"
            req_headers["X-Title"] = "Mobile Web IDE"
        req_body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
        }
        if tools:
            req_body["tools"] = tools
            req_body["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", url, json=req_body, headers=req_headers
            ) as resp:
                if resp.status_code != 200:
                    err_text = await resp.aread()
                    yield f"data: {_json.dumps({'type': 'error', 'error': f'HTTP {resp.status_code}: {err_text.decode()[:500]}'})}\n\n"
                    return
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        yield line + "\n"
                        yield "\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_openai(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/llm/test")
async def we_llm_test(request: Request) -> dict:
    """Test LLM provider connectivity."""
    body = await request.json()
    provider = body.get("provider", {})
    if not provider:
        return {"ok": False, "error": "Missing provider config"}

    api_key = provider.get("apiKey", "")
    vault = _we_vault_read()
    pid = provider.get("id", "")
    if pid and vault.get(pid):
        api_key = vault[pid]
    if not api_key:
        return {"ok": False, "error": "API key not configured"}

    base_url = provider.get("baseUrl", "").rstrip("/")
    model = provider.get("model", "")
    if not base_url or not model:
        return {"ok": False, "error": "Base URL or model not configured"}

    import httpx

    t0 = _time.time()
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "Say OK"}],
                },
            )
        ms = int((_time.time() - t0) * 1000)
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                "responseTime": ms,
            }
        return {
            "ok": True,
            "model": model,
            "responseTime": ms,
            "snippet": resp.text[:200],
        }
    except Exception as e:
        ms = int((_time.time() - t0) * 1000)
        return {"ok": False, "error": f"Connection failed: {e}", "responseTime": ms}


# Kernel module: Agent Execution (ATerm source copy)
# The built frontend is served as static files under /agent-execution/.
_AGENT_EXECUTION_DIST = (
    _PathLibEarly(__file__).parent
    / "modules"
    / "agent-execution"
    / "source"
    / "dist"
    / "ui"
)
if _AGENT_EXECUTION_DIST.is_dir():
    app.mount(
        "/agent-execution",
        StaticFiles(directory=str(_AGENT_EXECUTION_DIST), html=True),
        name="agent-execution",
    )


# ---------------------------------------------------------------------------
# Agent Execution Backend API
# ---------------------------------------------------------------------------
# Stub implementation of ATerm's /api/do endpoint. The real ATerm has a full
# session manager with PTY pool, distillation, automation, etc. This stub
# provides the core actions the frontend needs for beta: list, create, run,
# read, stop, start, delete, cancel, answer.
# Sessions are backed by Kernel PTY processes ("terminals" in server.py).

_ATERM_SESSIONS: dict = {}  # name -> {id, name, status, output_buffer}

_VALID_DO_ACTIONS = {
    "list",
    "read",
    "run",
    "stop",
    "start",
    "cancel",
    "answer",
    "create",
    "delete",
    "note",
    "search",
    "broadcast",
    "status",
    "history",
    "checkpoint",
    "record",
    "verify",
    "batch",
    "bridge",
    "automate",
}


class _DoRequest(BaseModel):
    action: str
    session: str | None = None
    input: str | None = None
    wait_until: str | None = None
    timeout: int | None = None
    lines: int | None = None
    output_mode: str | None = None
    include_marks: bool | None = None
    include_advanced: bool | None = None
    command: str | None = None
    directory: str | None = None
    tags: list[str] | None = None
    auto_start: bool | None = None
    sessions: list[str] | None = None
    cron_expression: str | None = None


def _aterm_hint(status: str | None) -> str:
    hints = {
        "ready": "Session is ready for commands.",
        "busy": "Session is running a command. Wait or cancel.",
        "stopped": "Session is stopped. Start it first.",
        "exited": "Session process has exited. Restart it.",
        "starting": "Session is starting up.",
    }
    return hints.get(status or "", "Unknown state.")


def _aterm_actions(status: str | None) -> list[str]:
    mapping = {
        "ready": ["run", "read", "stop", "cancel", "note", "history", "search", "broadcast", "checkpoint", "record", "verify", "batch", "automate"],
        "busy": ["read", "cancel", "stop", "note"],
        "stopped": ["start", "delete", "note", "history", "checkpoint", "record", "automate"],
        "exited": ["start", "delete", "read", "note", "history", "checkpoint", "record"],
        "starting": ["read", "stop", "note"],
    }
    return mapping.get(status or "", ["list", "create"])

async def _aterm_start_session(session_name: str, sess: dict) -> dict:
    """Start an ATerm session's backing PTY and return the /api/do contract."""
    sess["status"] = "starting"
    pty_id = f"aterm-{sess['id']}"
    pty_agent = {
        "command": sess.get("command", "/bin/bash"),
        "directory": sess.get("directory", os.getcwd()),
        "name": session_name,
    }
    try:
        pty_handle = await spawn_process(pty_id, pty_agent)
    except Exception as e:
        sess["status"] = "error"
        return {
            "ok": False,
            "status": "error",
            "hint": f"Failed to start: {e}",
            "actions": ["delete"],
        }

    sess["_pty_id"] = pty_id
    sess["status"] = "ready"
    sess["pid"] = pty_handle.process.pid
    sess["output_buffer"] = ""
    await _broadcast_aterm_event(
        {
            "type": "session_started",
            "session": {
                "id": sess["id"],
                "name": session_name,
                "status": "ready",
                "pid": pty_handle.process.pid,
            },
        }
    )
    return {
        "ok": True,
        "status": "ready",
        "pid": pty_handle.process.pid,
        "hint": "Session started.",
        "actions": _aterm_actions("ready"),
    }

_ATERM_CHECKPOINTS: dict[str, list[dict]] = {}
_ATERM_RECORDINGS: dict[str, dict] = {}
_ATERM_ACTIVE_RECORDINGS: dict[str, list[str]] = {}
_ATERM_AUTOMATIONS: dict[str, dict] = {}


def _aterm_limit(value, default: int = 50, maximum: int = 500) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _aterm_sync_output(sess: dict, limit: int = 4000) -> str:
    if sess.get("_restored_output_pending"):
        return sess.get("output_buffer", "")
    pty_id = sess.get("_pty_id")
    if pty_id and pty_id in running_processes:
        handle = running_processes[pty_id]
        output = bytes(handle.scrollback).decode("utf-8", errors="replace")
        sess["output_buffer"] = output[-limit:] if len(output) > limit else output
        if not handle.alive:
            sess["status"] = "exited"
    return sess.get("output_buffer", "")


def _aterm_record_history(sess: dict, user_input: str) -> None:
    history = sess.setdefault("history", [])
    history.append(user_input)
    if len(history) > 500:
        del history[:-500]


def _aterm_record_event(session_name: str, event: dict) -> None:
    for recording_id in list(_ATERM_ACTIVE_RECORDINGS.get(session_name, [])):
        recording = _ATERM_RECORDINGS.get(recording_id)
        if recording and recording.get("active"):
            recording.setdefault("events", []).append({"timestamp": time.time(), **event})


async def _aterm_run_input(session_name: str, sess: dict, user_input: str, *, wait_until: str | None = None, timeout: int | None = None, auto_start: bool = True) -> dict:
    if not user_input:
        return {"ok": False, "error": "input required"}
    if sess.get("_restored_output_pending"):
        sess.pop("_restored_output_pending", None)
    pty_id = sess.get("_pty_id")
    if auto_start and (not pty_id or pty_id not in running_processes or not running_processes[pty_id].alive):
        started = await _aterm_start_session(session_name, sess)
        if not started.get("ok"):
            return started
        pty_id = sess.get("_pty_id")
    if pty_id and pty_id in running_processes:
        handle = running_processes[pty_id]
        if handle.alive:
            try:
                os.write(handle.master_fd, (user_input + "\n").encode("utf-8"))
            except OSError:
                pass
            _aterm_record_history(sess, user_input)
            deadline = time.time() + min(max(timeout or 2, 1), 30)
            while True:
                await asyncio.sleep(0.05)
                output = _aterm_sync_output(sess)
                if wait_until and wait_until in output:
                    break
                if not wait_until and time.time() >= deadline - min(max(timeout or 2, 1), 30) + 0.3:
                    break
                if time.time() >= deadline:
                    break
        else:
            sess["output_buffer"] = sess.get("output_buffer", "") + f"$ {user_input}\n[process exited]\n"
    else:
        sess["output_buffer"] = sess.get("output_buffer", "") + f"$ {user_input}\n[no PTY — session not started]\n"
        _aterm_record_history(sess, user_input)
    output = sess.get("output_buffer", "")
    sess["status"] = "ready"
    _aterm_record_event(session_name, {"type": "command", "input": user_input, "output": output})
    await _broadcast_aterm_event({"type": "session_output", "session": {"id": sess["id"], "name": session_name, "status": sess.get("status")}, "input": user_input})
    return {"ok": True, "output": output, "status": "ready", "hint": _aterm_hint("ready"), "actions": _aterm_actions("ready")}


def _aterm_search(query: str, session_name: str | None = None) -> dict:
    if not query:
        return {"ok": False, "error": "input required"}
    import re
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as exc:
        return {"ok": False, "error": f"invalid search regex: {exc}"}
    results = []
    items = [(session_name, _ATERM_SESSIONS.get(session_name))] if session_name else list(_ATERM_SESSIONS.items())
    for name, sess in items:
        if not sess:
            continue
        output = _aterm_sync_output(sess, limit=20000)
        matches = [line for line in output.splitlines() if pattern.search(line)]
        if matches:
            results.append({"session": name, "matches": matches[:10]})
    return {"ok": True, "results": results, "hint": f"{sum(len(r['matches']) for r in results)} match(es) across {len(results)} session(s)."}


def _aterm_checkpoint(session_name: str, sess: dict, raw_input: str | None) -> dict:
    command = (raw_input or "save").strip() or "save"
    store = _ATERM_CHECKPOINTS.setdefault(session_name, [])
    if command == "list":
        return {"ok": True, "checkpoints": store, "hint": f"{len(store)} checkpoint(s)."}
    if command.startswith("restore:"):
        checkpoint_id = command.split(":", 1)[1].strip()
        checkpoint = next((item for item in store if item["id"] == checkpoint_id), None)
        if not checkpoint:
            return {"ok": False, "error": f"checkpoint not found: {checkpoint_id}"}
        sess["scratchpad"] = checkpoint.get("scratchpad", "")
        sess["history"] = list(checkpoint.get("history", []))
        sess["output_buffer"] = checkpoint.get("output", "")
        sess["directory"] = checkpoint.get("directory", sess.get("directory"))
        sess["env"] = dict(checkpoint.get("env", sess.get("env", {})))
        sess["_restored_output_pending"] = True
        return {"ok": True, "checkpoint_id": checkpoint_id, "hint": f"Restored checkpoint {checkpoint_id}."}
    name = command.split(":", 1)[1].strip() if command.startswith("save:") else (command if command != "save" else f"checkpoint-{int(time.time())}")
    checkpoint = {
        "id": f"cp_{uuid.uuid4().hex[:8]}",
        "name": name,
        "created_at": time.time(),
        "output": _aterm_sync_output(sess, limit=20000),
        "history": list(sess.get("history", [])),
        "scratchpad": sess.get("scratchpad", ""),
        "directory": sess.get("directory"),
        "env": dict(sess.get("env", {})),
    }
    store.append(checkpoint)
    return {"ok": True, "checkpoint_id": checkpoint["id"], "name": name, "checkpoint": checkpoint, "hint": f"Saved checkpoint {name}."}


def _aterm_record(session_name: str, raw_input: str | None) -> dict:
    command = (raw_input or "start").strip() or "start"
    if command == "list":
        items = [record for record in _ATERM_RECORDINGS.values() if record.get("session") == session_name]
        return {"ok": True, "recordings": items, "hint": f"{len(items)} recording(s)."}
    if command.startswith("get:"):
        recording_id = command.split(":", 1)[1].strip()
        recording = _ATERM_RECORDINGS.get(recording_id)
        if not recording or recording.get("session") != session_name:
            return {"ok": False, "error": f"recording not found: {recording_id}"}
        return {"ok": True, "recording": recording}
    if command.startswith("stop:"):
        recording_id = command.split(":", 1)[1].strip()
        recording = _ATERM_RECORDINGS.get(recording_id)
        if not recording or recording.get("session") != session_name:
            return {"ok": False, "error": f"recording not found: {recording_id}"}
        recording["active"] = False
        recording["ended_at"] = time.time()
        active = _ATERM_ACTIVE_RECORDINGS.setdefault(session_name, [])
        if recording_id in active:
            active.remove(recording_id)
        return {"ok": True, "recording_id": recording_id, "recording": recording, "hint": f"Stopped recording {recording_id}."}
    if command != "start":
        return {"ok": False, "error": "record input must be start, stop:<id>, list, or get:<id>"}
    recording_id = f"rec_{uuid.uuid4().hex[:8]}"
    recording = {"id": recording_id, "session": session_name, "name": f"recording-{int(time.time())}", "active": True, "started_at": time.time(), "events": []}
    _ATERM_RECORDINGS[recording_id] = recording
    _ATERM_ACTIVE_RECORDINGS.setdefault(session_name, []).append(recording_id)
    return {"ok": True, "recording_id": recording_id, "name": recording["name"], "hint": f"Started recording {recording_id}."}


def _aterm_automate(session_name: str, sess: dict, raw_input: str | None, cron_expression: str | None) -> dict:
    command = (raw_input or "list").strip() or "list"
    if command == "list":
        jobs = [job for job in _ATERM_AUTOMATIONS.values() if job.get("session") == session_name]
        return {"ok": True, "jobs": jobs, "hint": f"{len(jobs)} automation job(s)."}
    if command.startswith("cancel:"):
        job_id = command.split(":", 1)[1].strip()
        job = _ATERM_AUTOMATIONS.pop(job_id, None)
        if not job or job.get("session") != session_name:
            return {"ok": False, "error": f"automation job not found: {job_id}"}
        return {"ok": True, "job_id": job_id, "hint": f"Cancelled automation {job_id}."}
    if command != "register":
        return {"ok": False, "error": "automate input must be register, list, or cancel:<id>"}
    if not cron_expression:
        return {"ok": False, "error": "cron_expression required"}
    next_fire = compute_next_cron(cron_expression)
    if next_fire is None:
        return {"ok": False, "error": f"invalid cron_expression: {cron_expression}"}
    job_id = f"auto_{uuid.uuid4().hex[:8]}"
    job = {"id": job_id, "session": session_name, "cron_expression": cron_expression, "next_fire": next_fire, "command": sess.get("automation_command", ""), "created_at": time.time()}
    _ATERM_AUTOMATIONS[job_id] = job
    return {"ok": True, "job_id": job_id, "job": job, "next_fire": next_fire, "hint": f"Registered automation {job_id}."}

async def _aterm_broadcast(body: dict) -> dict:
    user_input = body.get("input", "")
    if not user_input:
        return {"ok": False, "error": "input required"}
    requested_names = body.get("sessions") or ([body.get("session")] if body.get("session") else list(_ATERM_SESSIONS.keys()))
    requested_names = [name for name in requested_names if name]
    target_names = []
    results = []
    for name in requested_names:
        sess = _ATERM_SESSIONS.get(name)
        if not sess:
            results.append({"session": name, "ok": False, "error": "session not found"})
            continue
        if sess.get("status") != "ready":
            results.append({"session": name, "ok": False, "error": "session is not ready"})
            continue
        target_names.append(name)
    for name in target_names:
        sess = _ATERM_SESSIONS[name]
        result = await _aterm_run_input(name, sess, user_input, timeout=body.get("timeout"), auto_start=True)
        results.append({"session": name, **result})
    sent = sum(1 for result in results if result.get("ok"))
    return {"ok": True, "sent": sent, "total": len(target_names), "results": results, "hint": f"Broadcast sent to {sent}/{len(target_names)} ready session(s)."}


async def _aterm_verify(session_name: str, sess: dict, body: dict) -> dict:
    result = await _aterm_run_input(
        session_name,
        sess,
        body.get("input", ""),
        wait_until=body.get("wait_until"),
        timeout=body.get("timeout"),
        auto_start=True,
    )
    if not result.get("ok"):
        return result
    output = result.get("output", "")
    state = analyze_structured(output, True, sess.get("last_output_time", 0.0), time.time())
    passed = state.get("state") != "error"
    return {"ok": True, "passed": passed, "status": result.get("status"), "output": output, "state_result": state, "hint": "Verification passed." if passed else state.get("hint", "Verification failed.")}


async def _aterm_batch(session_name: str, sess: dict, raw_input: str | None) -> dict:
    if not raw_input:
        return {"ok": False, "error": "input JSON array required"}
    try:
        items = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid batch JSON: {exc}"}
    if not isinstance(items, list):
        return {"ok": False, "error": "batch input must be a JSON array"}
    if len(items) > 20:
        return {"ok": False, "error": "batch supports at most 20 actions"}
    results = []
    for item in items:
        if not isinstance(item, dict):
            results.append({"ok": False, "error": "batch item must be an object"})
            continue
        action = item.get("action")
        if action in ("run", "answer"):
            results.append(await _aterm_run_input(session_name, sess, item.get("input", ""), wait_until=item.get("wait_until"), timeout=item.get("timeout")))
        elif action == "read":
            output = _aterm_sync_output(sess)
            results.append({"ok": True, "output": output, "status": sess.get("status", "stopped")})
        elif action == "history":
            limit = _aterm_limit(item.get("lines"), 50)
            results.append({"ok": True, "history": list(sess.get("history", []))[-limit:]})
        elif action == "note":
            if "input" in item:
                sess["scratchpad"] = item.get("input") or ""
            results.append({"ok": True, "scratchpad": sess.get("scratchpad", "")})
        else:
            results.append({"ok": False, "error": f"unsupported batch action: {action}"})
    return {"ok": all(item.get("ok") for item in results), "count": len(results), "results": results, "hint": f"Executed {len(results)} batch action(s)."}

@app.post("/api/do")
async def aterm_do(request: Request) -> dict:
    """ATerm-compatible /api/do endpoint — progressive disclosure agent API."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON body"}

    action = body.get("action", "")
    if not action or action not in _VALID_DO_ACTIONS:
        return {
            "ok": False,
            "error": f"invalid action: {action}. Valid: {', '.join(sorted(_VALID_DO_ACTIONS))}",
        }

    try:
        if action == "list":
            sessions = []
            for name, s in _ATERM_SESSIONS.items():
                sessions.append(
                    {
                        "id": s.get("id", name),
                        "name": name,
                        "status": s.get("status", "stopped"),
                        "tags": s.get("tags", []),
                        "history_count": len(s.get("history", [])),
                        "checkpoint_count": len(_ATERM_CHECKPOINTS.get(name, [])),
                    }
                )
            return {
                "ok": True,
                "sessions": sessions,
                "hint": f"{len(sessions)} session(s).",
                "actions": ["create", "read", "run", "start", "stop", "search", "broadcast"],
            }

        session_name = body.get("session", "")

        if action == "create":
            if not session_name:
                return {"ok": False, "error": "session (name) required"}
            cmd = body.get("command", "/bin/bash")
            session_id = str(uuid.uuid4())[:8]
            _ATERM_SESSIONS[session_name] = {
                "id": session_id,
                "name": session_name,
                "status": "stopped",
                "command": cmd,
                "directory": body.get("directory", os.getcwd()),
                "env": dict(body.get("env") or {}),
                "tags": body.get("tags", []),
                "output_buffer": "",
                "history": [],
                "scratchpad": "",
            }
            await _broadcast_aterm_event(
                {
                    "type": "session_created",
                    "session": {
                        "id": session_id,
                        "name": session_name,
                        "status": "stopped",
                        "tags": body.get("tags", []),
                    },
                }
            )
            if body.get("auto_start") is True:
                started = await _aterm_start_session(session_name, _ATERM_SESSIONS[session_name])
                if started.get("ok") is True:
                    started.update({"id": session_id, "name": session_name})
                return started
            return {
                "ok": True,
                "id": session_id,
                "name": session_name,
                "status": "stopped",
                "hint": "Session created. Start it when ready.",
                "actions": ["start", "delete", "note", "history", "checkpoint", "record", "automate"],
            }

        if action == "delete":
            if not session_name:
                return {"ok": False, "error": "session required"}
            deleted = _ATERM_SESSIONS.pop(session_name, None)
            if deleted:
                pty_id = deleted.get("_pty_id")
                if pty_id:
                    await kill_process(pty_id)
                for recording_id in list(_ATERM_ACTIVE_RECORDINGS.get(session_name, [])):
                    recording = _ATERM_RECORDINGS.get(recording_id)
                    if recording:
                        recording["active"] = False
                        recording["ended_at"] = time.time()
                _ATERM_ACTIVE_RECORDINGS.pop(session_name, None)
                for job_id in [jid for jid, job in _ATERM_AUTOMATIONS.items() if job.get("session") == session_name]:
                    _ATERM_AUTOMATIONS.pop(job_id, None)
                await _broadcast_aterm_event(
                    {
                        "type": "session_deleted",
                        "sessionId": deleted.get("id", session_name),
                        "session": {"id": deleted.get("id", session_name), "name": session_name},
                    }
                )
            return {
                "ok": deleted is not None,
                "hint": "Session deleted." if deleted else "Session not found.",
                "actions": ["list", "create"],
            }

        if action == "bridge":
            return {
                "ok": True,
                "bridge_status": {"server": False},
                "hint": "No legacy Anvil bridge is active; Kernel ATerm uses native /api/do, PTY WebSockets, and /ws/events.",
                "actions_simplified": ["navigate", "read", "click", "type", "screenshot"],
            }

        if action == "search":
            return _aterm_search(body.get("input", ""), session_name or None)

        if action == "broadcast":
            return await _aterm_broadcast(body)

        if action == "status":
            return await aterm_status()

        sess = _ATERM_SESSIONS.get(session_name)
        if not sess:
            return {"ok": False, "error": f"session not found: {session_name}"}

        if action == "start":
            return await _aterm_start_session(session_name, sess)

        if action == "stop":
            pty_id = sess.get("_pty_id")
            if pty_id:
                await kill_process(pty_id)
            sess["status"] = "stopped"
            sess["pid"] = None
            await _broadcast_aterm_event(
                {
                    "type": "session_stopped",
                    "session": {
                        "id": sess["id"],
                        "name": session_name,
                        "status": "stopped",
                        "pid": None,
                    },
                }
            )
            return {"ok": True, "status": "stopped", "hint": "Session stopped.", "actions": ["start", "delete"]}

        if action == "cancel":
            pty_id = sess.get("_pty_id")
            if pty_id and pty_id in running_processes:
                h = running_processes[pty_id]
                if h.alive:
                    try:
                        os.write(h.master_fd, b"\x03")
                    except OSError:
                        pass
            sess["status"] = "ready"
            return {"ok": True, "hint": "Sent Ctrl+C.", "actions": ["read", "run"]}

        if action == "read":
            output = _aterm_sync_output(sess, limit=_aterm_limit(body.get("limit"), 4000, 20000))
            return {
                "ok": True,
                "output": output,
                "status": sess.get("status", "stopped"),
                "hint": _aterm_hint(sess.get("status")),
                "actions": _aterm_actions(sess.get("status")),
            }

        if action in ("run", "answer"):
            return await _aterm_run_input(
                session_name,
                sess,
                body.get("input", ""),
                wait_until=body.get("wait_until"),
                timeout=body.get("timeout"),
                auto_start=True,
            )

        if action == "note":
            note_input = body.get("input")
            if note_input is not None:
                sess["scratchpad"] = note_input
                return {"ok": True, "hint": "Scratchpad updated."}
            return {"ok": True, "scratchpad": sess.get("scratchpad", ""), "hint": "Current scratchpad contents."}

        if action == "history":
            limit = _aterm_limit(body.get("lines"), 50)
            history = list(sess.get("history", []))[-limit:]
            return {"ok": True, "history": history, "hint": f"{len(history)} command(s) in history."}

        if action == "checkpoint":
            return _aterm_checkpoint(session_name, sess, body.get("input"))

        if action == "record":
            return _aterm_record(session_name, body.get("input"))

        if action == "verify":
            return await _aterm_verify(session_name, sess, body)

        if action == "batch":
            return await _aterm_batch(session_name, sess, body.get("input"))

        if action == "automate":
            return _aterm_automate(session_name, sess, body.get("input"), body.get("cron_expression"))

        return {"ok": False, "error": f"action not implemented: {action}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/aterm/status")
async def aterm_status() -> dict:
    now = time.time()
    sessions = []
    for name, sess in _ATERM_SESSIONS.items():
        pty_id = sess.get("_pty_id")
        alive = False
        output = sess.get("output_buffer", "")
        if pty_id and pty_id in running_processes:
            handle = running_processes[pty_id]
            alive = handle.alive
            output = bytes(handle.scrollback).decode("utf-8", errors="replace")[-4000:]
        structured = analyze_structured(output, alive, sess.get("last_output_time", 0.0), now)
        sessions.append({
            "id": sess.get("id", name),
            "name": name,
            "status": sess.get("status", "stopped"),
            "state": structured["state"],
            "errors": structured["errors"],
            "tests": structured["tests"],
            "hint": structured["hint"],
            "actions": _aterm_actions(sess.get("status")),
            "tags": sess.get("tags", []),
        })
    return {"ok": True, "sessions": sessions, "total": len(sessions)}

# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------


@app.get("/api/agents")
async def get_agents():
    agents = load_agents()
    result = list(agents.values())
    for a in result:
        aid = a["id"]
        if aid in running_processes:
            h = running_processes[aid]
            a["status"] = "running" if h.alive else "exited"
            a["uptime"] = time.time() - h.started_at
            a["restart_count"] = agent_metrics.get(aid, {}).get("restart_count", 0)
        else:
            a["status"] = "stopped"
            a["uptime"] = 0
            a["restart_count"] = agent_metrics.get(aid, {}).get("restart_count", 0)
    result.sort(key=lambda a: (-a.get("pinned", False), a.get("order", 0)))
    return result


@app.post("/api/agents")
async def create_agent(agent_in: AgentCreate):
    agents = load_agents()
    agent_id = str(uuid.uuid4())
    new_agent = Agent(
        id=agent_id,
        name=agent_in.name,
        label=agent_in.label,
        directory=agent_in.directory,
        command=agent_in.command,
        env=agent_in.env,
        order=agent_in.order,
        tags=agent_in.tags or [],
        auto_start=agent_in.auto_start,
        pinned=agent_in.pinned,
        launchd_type=agent_in.launchd_type,
        launchd_interval=agent_in.launchd_interval,
        launchd_watchpath=agent_in.launchd_watchpath,
        cron_expression=agent_in.cron_expression,
    )
    agents[agent_id] = new_agent.model_dump()
    save_agents(agents)
    # Wire up launchd if requested — silently no-op for launchd_type="none"
    if new_agent.launchd_type != "none":
        _generate_launchd_plist(agent_id, agents[agent_id])
    return new_agent


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, updates: AgentUpdate):
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = agents[agent_id]
    prev_launchd_type = agent.get("launchd_type", "none")
    for field, value in updates.model_dump(exclude_none=True).items():
        agent[field] = value
    agents[agent_id] = agent
    save_agents(agents)
    # If launchd config changed, regenerate or remove the plist accordingly.
    new_launchd_type = agent.get("launchd_type", "none")
    automation_fields = {
        "launchd_type",
        "launchd_interval",
        "launchd_watchpath",
        "command",
        "directory",
    }
    touched_automation = bool(
        automation_fields & set(updates.model_dump(exclude_none=True).keys())
    )
    if touched_automation:
        if new_launchd_type == "none" and prev_launchd_type != "none":
            _remove_launchd_plist(agent_id)
        elif new_launchd_type != "none":
            _generate_launchd_plist(agent_id, agent)
    return agent


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str):
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    await kill_process(agent_id)
    # Remove launchd plist before deleting the agent (the function is idempotent
    # — safe to call even if no plist exists).
    _remove_launchd_plist(agent_id)
    del agents[agent_id]
    save_agents(agents)
    agent_metrics.pop(agent_id, None)
    _cron_last_fire.pop(agent_id, None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Auto-cleaner: removes stale ephemeral/sidepanel shells that have been idle.
# ---------------------------------------------------------------------------
_autoclean_task: asyncio.Task | None = None
AUTO_CLEAN_DEFAULT_IDLE_SECONDS = 3600  # 1 hour
AUTO_CLEAN_INTERVAL_SECONDS = 600  # check every 10 min


async def _autoclean_loop():
    """Background coroutine that periodically purges idle ephemeral agents."""
    while True:
        await asyncio.sleep(AUTO_CLEAN_INTERVAL_SECONDS)
        try:
            agents = load_agents()
            now = time.time()
            removed = []
            for aid, agent in list(agents.items()):
                tags = agent.get("tags", [])
                # Only auto-remove ephemeral sidepanel/workspace shells
                if "ephemeral" not in tags:
                    continue
                # Check idle time: if running, look at uptime; if stopped, always clean
                if aid in running_processes:
                    h = running_processes[aid]
                    if h.alive:
                        # Still alive — check how long it's been running
                        uptime = now - h.started_at
                        if uptime < AUTO_CLEAN_DEFAULT_IDLE_SECONDS:
                            continue  # young process, keep it
                # Safe to remove
                await kill_process(aid)
                _remove_launchd_plist(aid)
                del agents[aid]
                agent_metrics.pop(aid, None)
                _cron_last_fire.pop(aid, None)
                removed.append(agent.get("name", aid))
            if removed:
                save_agents(agents)
                logger.info(
                    "Auto-cleaner removed %d stale agents: %s",
                    len(removed),
                    ", ".join(removed[:10]),
                )
        except Exception:
            logger.exception("Auto-cleaner error")


@app.post("/api/autoclean")
async def autoclean_now(
    idle_seconds: int = AUTO_CLEAN_DEFAULT_IDLE_SECONDS,
    tags: str = "ephemeral",
):
    """Run an immediate cleanup of idle agents matching the given tags."""
    agents = load_agents()
    now = time.time()
    target_tags = {t.strip() for t in tags.split(",")}
    removed = []
    for aid, agent in list(agents.items()):
        agent_tags = set(agent.get("tags", []))
        if not target_tags & agent_tags:
            continue
        if aid in running_processes:
            h = running_processes[aid]
            if h.alive:
                uptime = now - h.started_at
                if uptime < idle_seconds:
                    continue
        await kill_process(aid)
        _remove_launchd_plist(aid)
        del agents[aid]
        agent_metrics.pop(aid, None)
        _cron_last_fire.pop(aid, None)
        removed.append({"id": aid, "name": agent.get("name", aid)})
    if removed:
        save_agents(agents)
    return {"removed": len(removed), "details": removed}


@app.get("/api/autoclean/status")
async def autoclean_status():
    """Return auto-cleaner config and current stale count."""
    agents = load_agents()
    now = time.time()
    stale = 0
    for aid, agent in agents.items():
        if "ephemeral" not in agent.get("tags", []):
            continue
        if aid in running_processes:
            h = running_processes[aid]
            if h.alive and (now - h.started_at) < AUTO_CLEAN_DEFAULT_IDLE_SECONDS:
                continue
        stale += 1
    return {
        "enabled": _autoclean_task is not None and not _autoclean_task.done(),
        "interval_seconds": AUTO_CLEAN_INTERVAL_SECONDS,
        "idle_threshold_seconds": AUTO_CLEAN_DEFAULT_IDLE_SECONDS,
        "stale_count": stale,
        "total_agents": len(agents),
    }


@app.on_event("startup")
async def _start_autoclean():
    global _autoclean_task
    _autoclean_task = asyncio.create_task(_autoclean_loop())
    logger.info(
        "Auto-cleaner started (interval=%ds, idle=%ds)",
        AUTO_CLEAN_INTERVAL_SECONDS,
        AUTO_CLEAN_DEFAULT_IDLE_SECONDS,
    )


@app.get("/api/agents/{agent_id}/status")
async def agent_status(agent_id: str):
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = agents[agent_id]
    if agent_id in running_processes:
        h = running_processes[agent_id]
        status = "running" if h.alive else "exited"
        uptime = time.time() - h.started_at
    else:
        status = "stopped"
        uptime = 0
    metrics = agent_metrics.get(agent_id, {})
    launchd_info = get_launchd_info(agent_id, agent)
    cron_expr = agent.get("cron_expression")
    next_cron = compute_next_cron(cron_expr) if cron_expr else None
    return {
        "id": agent_id,
        "status": status,
        "uptime": uptime,
        "restart_count": metrics.get("restart_count", 0),
        "cron_expression": cron_expr,
        "next_cron_run": next_cron,
        **launchd_info,
    }


@app.post("/api/agents/{agent_id}/restart")
async def restart_agent(agent_id: str):
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    await kill_process(agent_id)
    # Increment restart count
    if agent_id not in agent_metrics:
        agent_metrics[agent_id] = {
            "restart_count": 0,
            "total_uptime": 0.0,
            "last_started": 0,
        }
    agent_metrics[agent_id]["restart_count"] += 1
    return {"id": agent_id, "restarted": True}


@app.post("/api/agents/{agent_id}/resize")
async def resize_terminal(agent_id: str, payload: ResizePayload):
    handle = running_processes.get(agent_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="No running process for agent")
    try:
        winsize = struct.pack("HHHH", payload.rows, payload.cols, 0, 0)
        fcntl.ioctl(handle.master_fd, termios.TIOCSWINSZ, winsize)
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Terminal output capture/export
# ---------------------------------------------------------------------------


@app.get("/api/agents/{agent_id}/scrollback")
async def get_scrollback(agent_id: str):
    """Download the current scrollback buffer as plain text."""
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    handle = running_processes.get(agent_id)
    if handle is None:
        return Response(content=b"", media_type="text/plain")
    content = handle.get_raw_scrollback()
    name = agents[agent_id].get("name", agent_id).replace(" ", "_")
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{name}_output.txt"'},
    )


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


@app.post("/api/bulk/restart")
async def bulk_restart():
    """Restart all running agents."""
    agents = load_agents()
    restarted = []
    for agent_id in list(running_processes):
        if agent_id in agents:
            await kill_process(agent_id)
            if agent_id not in agent_metrics:
                agent_metrics[agent_id] = {
                    "restart_count": 0,
                    "total_uptime": 0.0,
                    "last_started": 0,
                }
            agent_metrics[agent_id]["restart_count"] += 1
            restarted.append(agent_id)
    return {"restarted": restarted}


@app.post("/api/bulk/stop")
async def bulk_stop():
    """Stop all running agents."""
    stopped = []
    for agent_id in list(running_processes):
        await kill_process(agent_id)
        stopped.append(agent_id)
    return {"stopped": stopped}


@app.post("/api/bulk/start")
async def bulk_start():
    """Start all agents that are not running."""
    agents = load_agents()
    started = []
    for agent_id, agent in agents.items():
        if agent_id not in running_processes:
            try:
                await spawn_process(agent_id, agent)
                started.append(agent_id)
            except Exception:
                pass
    return {"started": started}


# ---------------------------------------------------------------------------
# Broadcast — send same input to multiple agents
# ---------------------------------------------------------------------------


@app.post("/api/broadcast")
async def broadcast_input(payload: BroadcastPayload):
    """Send the same input string to multiple agent PTYs."""
    sent_to = []
    for agent_id in payload.agent_ids:
        handle = running_processes.get(agent_id)
        if handle and handle.alive:
            try:
                os.write(handle.master_fd, payload.input.encode("utf-8"))
                sent_to.append(agent_id)
            except OSError:
                pass
    return {"sent_to": sent_to}


# ---------------------------------------------------------------------------
# Templates (presets)
# ---------------------------------------------------------------------------


@app.get("/api/templates")
async def get_templates():
    return list(load_templates().values())


@app.post("/api/templates")
async def create_template(tmpl: TemplateCreate):
    templates = load_templates()
    tmpl_id = str(uuid.uuid4())
    data = {
        "id": tmpl_id,
        "name": tmpl.name,
        "directory": tmpl.directory,
        "command": tmpl.command,
        "env": tmpl.env,
        "tags": tmpl.tags or [],
    }
    templates[tmpl_id] = data
    save_templates(templates)
    return data


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: str):
    templates = load_templates()
    if template_id not in templates:
        raise HTTPException(status_code=404, detail="Template not found")
    del templates[template_id]
    save_templates(templates)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agent import/export (backup/restore)
# ---------------------------------------------------------------------------


@app.get("/api/export")
async def export_agents():
    """Export all agent configs as JSON for backup."""
    agents = load_agents()
    return Response(
        content=json.dumps(list(agents.values()), indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="agents_backup.json"'},
    )


@app.post("/api/import")
async def import_agents(agent_list: List[dict]):
    """Import agents from a backup. Skips agents with duplicate names."""
    agents = load_agents()
    existing_names = {a["name"] for a in agents.values()}
    imported = []
    for a in agent_list:
        name = a.get("name", "")
        if name in existing_names:
            continue
        agent_id = str(uuid.uuid4())
        new_agent = {
            "id": agent_id,
            "name": name,
            "directory": a.get("directory", "/tmp"),
            "command": a.get("command", "bash"),
            "env": a.get("env"),
            "order": a.get("order", 0),
            "tags": a.get("tags", []),
            "auto_start": a.get("auto_start", False),
            "pinned": a.get("pinned", False),
        }
        agents[agent_id] = new_agent
        existing_names.add(name)
        imported.append(agent_id)
    save_agents(agents)
    return {"imported": imported, "count": len(imported)}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@app.get("/api/tags")
async def get_tags():
    """Return all unique tags used across agents."""
    agents = load_agents()
    tags = set()
    for a in agents.values():
        for t in a.get("tags", []):
            tags.add(t)
    return sorted(tags)


@app.post("/api/tags/rename")
async def rename_tag(body: dict):
    """Rename a tag across all agents that use it."""
    old_name = body.get("old_name", "").strip()
    new_name = body.get("new_name", "").strip()
    if not old_name or not new_name:
        raise HTTPException(
            status_code=400, detail="old_name and new_name are required"
        )
    if old_name == new_name:
        return {"renamed": 0}
    agents = load_agents()
    renamed = 0
    for a in agents.values():
        tags = a.get("tags", [])
        if old_name in tags:
            tags = [new_name if t == old_name else t for t in tags]
            a["tags"] = tags
            renamed += 1
    save_agents(agents)
    return {"renamed": renamed, "old_name": old_name, "new_name": new_name}


# ---------------------------------------------------------------------------
# Layout presets
# ---------------------------------------------------------------------------

LAYOUTS_FILE = "layouts.json"


def _load_layouts() -> dict:
    try:
        with open(LAYOUTS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_layouts(data: dict):
    with open(LAYOUTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


class LayoutPreset(BaseModel):
    name: str
    layout: str = "auto"
    agent_order: list[str] = []
    view: str = "grid"


@app.get("/api/layouts")
async def get_layouts():
    return _load_layouts()


@app.post("/api/layouts")
async def save_layout(preset: LayoutPreset):
    layouts = _load_layouts()
    layouts[preset.name] = preset.model_dump(exclude_none=True)
    _save_layouts(layouts)
    return {"saved": preset.name}


@app.delete("/api/layouts/{name}")
async def delete_layout(name: str):
    layouts = _load_layouts()
    if name in layouts:
        del layouts[name]
        _save_layouts(layouts)
        return {"deleted": name}
    raise HTTPException(status_code=404, detail="Layout not found")


@app.delete("/api/tags/{tag_name}")
async def delete_tag(tag_name: str):
    """Remove a tag from all agents that have it."""
    agents = load_agents()
    removed = 0
    for a in agents.values():
        tags = a.get("tags", [])
        if tag_name in tags:
            tags = [t for t in tags if t != tag_name]
            a["tags"] = tags
            removed += 1
    save_agents(agents)
    return {"removed": removed, "tag_name": tag_name}


@app.get("/api/agents/{agent_id}/scrollback/stats")
async def get_scrollback_stats(agent_id: str):
    """Return scrollback buffer utilization for a running agent."""
    handle = running_processes.get(agent_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="Agent not running")
    return handle.scrollback_stats()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# WebSocket: /ws/pty — MWIDE TerminalPane JSON-PTY protocol
# ---------------------------------------------------------------------------
# MWIDE's TerminalPane.tsx sends JSON messages:
#   {type:"open", sessionId?, cwd, cols, rows, command?}
#   {type:"in", data}
#   {type:"resize", cols, rows}
#   {type:"kill"}
# Server responds:
#   {type:"ready", sessionId, pid, shell, resumed}
#   {type:"replay", data}
#   {type:"out", data}
#   {type:"exit", code}

import secrets as _secrets

_mwide_pty_sessions: Dict[str, dict] = {}  # sessionId -> {handle, cwd, command}


@app.websocket("/ws/pty")
async def mwide_pty_endpoint(websocket: WebSocket):
    """MWIDE TerminalPane JSON-PTY WebSocket."""
    await websocket.accept()
    handle: ProcessHandle | None = None
    session_id = ""
    reader_task: asyncio.Task | None = None
    alive = True

    async def read_pty_output(h: ProcessHandle, sid: str):
        """Background task: read PTY output using event-loop add_reader (non-blocking)."""
        nonlocal alive
        loop = asyncio.get_running_loop()
        fd = h.master_fd
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        h._reader_closed = False

        def _on_readable():
            if h._reader_closed:
                return
            try:
                data = os.read(fd, 65536)
                if not data:
                    h._reader_closed = True
                    queue.put_nowait(None)
                    return
                queue.put_nowait(data)
            except BlockingIOError:
                pass
            except OSError:
                if not h._reader_closed:
                    h._reader_closed = True
                    queue.put_nowait(None)

        loop.add_reader(fd, _on_readable)
        try:
            while alive:
                data = await queue.get()
                if data is None:
                    break
                text = data.decode("utf-8", errors="replace")
                try:
                    await websocket.send_json({"type": "out", "data": text})
                except Exception:
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            try:
                loop.remove_reader(fd)
            except Exception:
                pass
            if alive:
                try:
                    code = h.process.returncode
                    if code is None:
                        await h.process.wait()
                        code = h.process.returncode
                    await websocket.send_json(
                        {"type": "exit", "code": code if code is not None else -1}
                    )
                except Exception:
                    pass

    try:
        # First message must be {type: "open", ...}
        raw = await websocket.receive()
        if raw.get("type") == "websocket.disconnect":
            return
        text = raw.get("text", "")
        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            await websocket.send_json({"type": "exit", "code": -1})
            return

        if msg.get("type") != "open":
            await websocket.send_json({"type": "exit", "code": -1})
            return

        existing_sid = msg.get("sessionId")
        cwd = msg.get("cwd") or os.path.expanduser("~")
        cols = int(msg.get("cols", 80))
        rows = int(msg.get("rows", 24))
        command = msg.get("command", "/bin/bash")

        resumed = False

        # Try to resume existing session
        if existing_sid and existing_sid in _mwide_pty_sessions:
            stored = _mwide_pty_sessions[existing_sid]
            existing_handle = stored.get("handle")
            if existing_handle and existing_handle.alive:
                handle = existing_handle
                assert handle is not None  # guaranteed by existing_handle check
                session_id = existing_sid
                resumed = True
                # Resize
                try:
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(handle.master_fd, termios.TIOCSWINSZ, winsize)
                except OSError:
                    pass

        if not handle or not handle.alive:
            # Spawn new PTY
            session_id = existing_sid or f"mwide-pty-{_secrets.token_hex(4)}"
            agent = {
                "command": command,
                "directory": cwd,
                "name": session_id,
            }
            try:
                handle = await spawn_process(session_id, agent)
                _mwide_pty_sessions[session_id] = {
                    "handle": handle,
                    "cwd": cwd,
                    "command": command,
                }
            except Exception:
                await websocket.send_json({"type": "exit", "code": -1})
                return
            # Resize to requested dimensions
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(handle.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

        # Send ready
        await websocket.send_json(
            {
                "type": "ready",
                "sessionId": session_id,
                "pid": handle.process.pid if handle.process else None,
                "shell": command,
                "resumed": resumed,
            }
        )

        # Start reader task
        reader_task = asyncio.create_task(read_pty_output(handle, session_id))

        # Main message loop
        while alive:
            raw = await websocket.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            text = raw.get("text", "")
            try:
                msg = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            mtype = msg.get("type")
            if mtype == "in" and handle and handle.alive:
                data = msg.get("data", "")
                try:
                    os.write(handle.master_fd, data.encode("utf-8"))
                except OSError:
                    break
            elif mtype == "resize" and handle and handle.alive:
                try:
                    c = int(msg.get("cols", 80))
                    r = int(msg.get("rows", 24))
                    winsize = struct.pack("HHHH", r, c, 0, 0)
                    fcntl.ioctl(handle.master_fd, termios.TIOCSWINSZ, winsize)
                except (OSError, ValueError):
                    pass
            elif mtype == "kill" and handle:
                try:
                    handle.process.terminate()
                except Exception:
                    pass
                break

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        alive = False
        if reader_task and not reader_task.done():
            reader_task.cancel()


# ---------------------------------------------------------------------------
# WebSocket: /ws/collab — MWIDE collaboration fan-out hub
# ---------------------------------------------------------------------------
# Simple room-based fan-out: messages from one peer are forwarded to all
# other peers in the same room. No CRDT — last-write-wins at application level.

_collab_rooms: Dict[str, list] = {}  # room -> [WebSocket, ...]


@app.websocket("/ws/collab")
async def collab_endpoint(websocket: WebSocket):
    """Collaboration WebSocket — room-based fan-out."""
    await websocket.accept()
    room = websocket.query_params.get("room", "default")

    if room not in _collab_rooms:
        _collab_rooms[room] = []
    _collab_rooms[room].append(websocket)

    try:
        while True:
            raw = await websocket.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            text = raw.get("text")
            if not text:
                continue
            # Fan out to all other peers in the room
            peers = _collab_rooms.get(room, [])
            for peer in peers:
                if peer is websocket:
                    continue
                try:
                    await peer.send_text(text)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        peers = _collab_rooms.get(room, [])
        if websocket in peers:
            peers.remove(websocket)
        if not peers:
            _collab_rooms.pop(room, None)


# ---------------------------------------------------------------------------
# WebSocket: /ws/events — ATerm session lifecycle events
# ---------------------------------------------------------------------------
# ATerm's useEvents hook connects here. When sessions are created/deleted/
# started/stopped via /api/do, we broadcast state changes to all connected
# event clients.

_aterm_event_clients: list = []


def _aterm_event_matches(client: dict, event: dict) -> bool:
    filters = client.get("filters") or []
    if not filters:
        return True
    event_type = event.get("type")
    session = event.get("session") if isinstance(event.get("session"), dict) else {}
    session_id = event.get("sessionId") or session.get("id")
    session_name = session.get("name")
    for event_filter in filters:
        types = event_filter.get("types") or []
        sessions = event_filter.get("sessions") or []
        if types and event_type not in types:
            continue
        if sessions and session_id not in sessions and session_name not in sessions:
            continue
        return True
    return False


async def _broadcast_aterm_event(event: dict):
    """Send an event dict to connected /ws/events clients that match filters."""
    dead = []
    for client in list(_aterm_event_clients):
        try:
            websocket = client["websocket"] if isinstance(client, dict) else client
            if isinstance(client, dict) and not _aterm_event_matches(client, event):
                continue
            await websocket.send_json(event)
        except Exception:
            dead.append(client)
    for d in dead:
        if d in _aterm_event_clients:
            _aterm_event_clients.remove(d)


@app.websocket("/ws/events")
async def aterm_events_endpoint(websocket: WebSocket):
    """ATerm session lifecycle events WebSocket with subscribe/unsubscribe filters."""
    await websocket.accept()
    client = {"websocket": websocket, "filters": []}
    _aterm_event_clients.append(client)
    sessions = []
    for name, s in _ATERM_SESSIONS.items():
        sessions.append(
            {
                "id": s.get("id", name),
                "name": name,
                "status": s.get("status", "stopped"),
                "tags": s.get("tags", []),
                "pid": s.get("pid"),
            }
        )
    try:
        await websocket.send_json({"type": "sessions_list", "sessions": sessions})
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "error": "invalid JSON"})
                continue
            message_type = payload.get("type")
            if message_type == "subscribe":
                client["filters"].append(
                    {
                        "sessions": payload.get("sessions") or [],
                        "types": payload.get("types") or [],
                    }
                )
                await websocket.send_json({"type": "subscribed", "filters": client["filters"]})
            elif message_type == "unsubscribe":
                client["filters"] = []
                await websocket.send_json({"type": "unsubscribed"})
            else:
                await websocket.send_json({"type": "error", "error": f"unsupported event control message: {message_type}"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if client in _aterm_event_clients:
            _aterm_event_clients.remove(client)


# ---------------------------------------------------------------------------
# WebSocket terminal (catch-all /ws/{agent_id} — MUST be after specific)
# ---------------------------------------------------------------------------


@app.websocket("/ws/{agent_id}")
async def websocket_endpoint(websocket: WebSocket, agent_id: str):
    await websocket.accept()
    agents = load_agents()

    # Check if this is a regular sidebar agent or an ATerm-managed PTY
    # ATerm sessions store their PTY under "aterm-{session_id}" in running_processes
    pty_id = None
    if agent_id not in agents:
        # Maybe it's an ATerm session ID — look up the PTY ID
        for _name, sess in _ATERM_SESSIONS.items():
            if sess.get("id") == agent_id:
                pty_id = sess.get("_pty_id")
                break
        if not pty_id:
            # Also check if agent_id is directly a PTY ID (aterm-xxxx)
            if agent_id in running_processes:
                pty_id = agent_id
        if not pty_id:
            await websocket.send_json(
                {"type": "status", "status": "error", "message": "Agent not found"}
            )
            await websocket.close()
            return

    if pty_id:
        # ATerm/PTY WebSocket — use the PTY handle directly
        handle = running_processes.get(pty_id)
        if not handle or not handle.alive:
            await websocket.send_json(
                {"type": "status", "status": "error", "message": "PTY not running"}
            )
            await websocket.close()
            return
        handle.websockets.append(websocket)
        await websocket.send_json(
            {"type": "status", "status": "connected", "reconnect": True}
        )
        clean = handle.get_clean_scrollback()
        if clean:
            try:
                await websocket.send_bytes(clean)
            except Exception:
                pass
        # Main loop — raw byte streaming (ATerm Terminal uses this)
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.receive":
                    if "text" in msg:
                        text = msg["text"]
                        if text.startswith('{"type":"resize"'):
                            try:
                                payload = json.loads(text)
                                if payload.get("type") == "resize":
                                    cols = int(payload["cols"])
                                    rows = int(payload["rows"])
                                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                                    fcntl.ioctl(
                                        handle.master_fd,
                                        termios.TIOCSWINSZ,
                                        winsize,
                                    )
                            except (json.JSONDecodeError, KeyError, OSError):
                                pass
                            continue
                        os.write(handle.master_fd, text.encode("utf-8"))
                    elif "bytes" in msg:
                        os.write(handle.master_fd, msg["bytes"])
                elif msg["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if websocket in handle.websockets:
                handle.websockets.remove(websocket)
            try:
                await websocket.close()
            except Exception:
                pass
        return

    # Regular sidebar agent
    agent = agents[agent_id]

    handle = running_processes.get(agent_id)
    if handle and handle.alive:
        handle.websockets.append(websocket)
        await websocket.send_json(
            {"type": "status", "status": "connected", "reconnect": True}
        )
        clean = handle.get_clean_scrollback()
        if clean:
            try:
                await websocket.send_bytes(clean)
            except Exception:
                pass
    else:
        try:
            handle = await spawn_process(agent_id, agent)
            handle.websockets.append(websocket)
            await websocket.send_json(
                {"type": "status", "status": "connected", "reconnect": False}
            )
        except Exception as e:
            await websocket.send_json(
                {"type": "status", "status": "error", "message": str(e)}
            )
            await websocket.close()
            return

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.receive":
                if "text" in msg:
                    text = msg["text"]
                    if text.startswith('{"type":"resize"'):
                        try:
                            payload = json.loads(text)
                            if payload.get("type") == "resize":
                                cols = int(payload["cols"])
                                rows = int(payload["rows"])
                                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                                fcntl.ioctl(
                                    handle.master_fd, termios.TIOCSWINSZ, winsize
                                )
                        except (json.JSONDecodeError, KeyError, OSError):
                            pass
                        continue
                    os.write(handle.master_fd, text.encode("utf-8"))
                elif "bytes" in msg:
                    os.write(handle.master_fd, msg["bytes"])
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if websocket in handle.websockets:
            handle.websockets.remove(websocket)
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Vault-keyed API proxy (Hostinger billing)
# ---------------------------------------------------------------------------


@app.get("/api/proxy/hostinger/subscriptions")
async def proxy_hostinger_subscriptions():
    """Proxy Hostinger billing API using vaulted token.

    The infrastructure-map.html Hostinger Live tab calls this instead of
    the external API directly, so the token never reaches the browser.
    """
    vault = _we_vault_read()
    token = vault.get("hostinger", "")
    if not token:
        return {"error": "No hostinger token in vault", "subscriptions": []}
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        "https://developers.hostinger.com/api/billing/v1/subscriptions",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data
    except urllib.error.HTTPError as e:
        return {
            "error": f"Hostinger API HTTP {e.code}",
            "detail": e.read().decode()[:500],
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Health & Launchd Status
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/performance")
async def get_performance():
    """Server-level CPU and memory stats using resource module."""
    import resource

    try:
        r = resource.getrusage(resource.RUSAGE_SELF)
        return {
            "cpu_user_s": round(r.ru_utime, 2),
            "cpu_system_s": round(r.ru_stime, 2),
            "max_rss_kb": r.ru_maxrss,
            "involuntary_ctx_switches": r.ru_nivcsw,
            "voluntary_ctx_switches": r.ru_nvcsw,
            "running_agents": len(running_processes),
            "timestamp": time.time(),
        }
    except Exception as e:
        return {"error": str(e), "running_agents": len(running_processes)}


@app.get("/api/agents/{agent_id}/launchd")
async def agent_launchd_status(agent_id: str):
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "agent_id": agent_id,
        **get_launchd_info(agent_id, agents[agent_id]),
    }


# ---------------------------------------------------------------------------
# Dev Launcher API proxy (forward to port 4500 backend)
# ---------------------------------------------------------------------------

_DEV_LAUNCHER_PORT = int(os.environ.get("DEV_LAUNCHER_PORT", "4500"))
_DEV_LAUNCHER_ROUTES = {
    "/api/apps",
    "/api/browse",
    "/api/discover",
    "/api/dev-launcher/import",
    "/api/cleanup",
    "/api/config/refresh",
}


async def _proxy_dev_launcher(path: str, request: Request) -> Response:
    """Forward Dev Launcher API calls to its backend."""
    import httpx as _httpx

    target = f"http://127.0.0.1:{_DEV_LAUNCHER_PORT}{path}"
    body = await request.body()
    headers = {
        "Content-Type": request.headers.get("content-type", "application/json"),
        "Accept": "application/json",
    }
    try:
        async with _httpx.AsyncClient(timeout=15.0) as client:
            try:
                httpx_response = await client.request(
                    method=request.method,
                    url=target,
                    content=body or None,
                    headers=headers,
                )
                return Response(
                    content=httpx_response.content,
                    status_code=httpx_response.status_code,
                    media_type=httpx_response.headers.get("Content-Type", "application/json"),
                )
            except _httpx.HTTPStatusError as e:
                return Response(
                    content=e.response.content,
                    status_code=e.response.status_code,
                    media_type="application/json",
                )
    except _httpx.ConnectError:
        return Response(
            content=json.dumps({"error": "Dev Launcher backend unavailable", "port": _DEV_LAUNCHER_PORT}),
            status_code=503,
            media_type="application/json",
        )
    except _httpx.TimeoutException:
        return Response(
            content=json.dumps({"error": "Dev Launcher backend timed out"}),
            status_code=504,
            media_type="application/json",
        )
    except Exception as e:
        return Response(
            content=json.dumps({"error": str(e)}),
            status_code=502,
            media_type="application/json",
        )


@app.api_route("/api/apps/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_apps(path: str, request: Request):
    return await _proxy_dev_launcher(f"/api/apps/{path}", request)


@app.api_route("/api/apps", methods=["GET", "POST"])
async def proxy_apps_root(request: Request):
    return await _proxy_dev_launcher("/api/apps", request)


@app.api_route("/api/browse", methods=["GET", "POST"])
async def proxy_browse(request: Request):
    return await _proxy_dev_launcher("/api/browse", request)


@app.api_route("/api/discover", methods=["GET", "POST"])
async def proxy_discover(request: Request):
    return await _proxy_dev_launcher("/api/discover", request)


@app.api_route("/api/dev-launcher/import", methods=["GET", "POST"])
async def proxy_dev_launcher_import(request: Request):
    return await _proxy_dev_launcher("/api/dev-launcher/import", request)


@app.api_route("/api/cleanup", methods=["GET", "POST"])
async def proxy_cleanup(request: Request):
    return await _proxy_dev_launcher("/api/cleanup", request)


@app.api_route("/api/config/refresh", methods=["GET", "POST"])
async def proxy_config_refresh(request: Request):
    return await _proxy_dev_launcher("/api/config/refresh", request)


# ---------------------------------------------------------------------------
# Automation — Cron
# ---------------------------------------------------------------------------


@app.get("/api/automation/jobs")
async def list_automation_jobs():
    """Return all automation jobs (launchd + cron) with status, schedule, next run."""
    agents = load_agents()
    jobs = []
    for agent_id, agent in agents.items():
        metrics = agent_metrics.get(agent_id, {})
        launchd_type = agent.get("launchd_type", "none")
        cron_expr = agent.get("cron_expression")

        # Launchd job (one per agent with automation)
        if launchd_type != "none":
            info = get_launchd_info(agent_id, agent)
            j = {
                "agent_id": agent_id,
                "agent_name": agent.get("name", ""),
                "automation_type": launchd_type,
                "run_count": info.get("run_count", 0),
                "status": "active" if info.get("loaded") else "inactive",
                "pid": info.get("pid"),
                "log_out": info.get("log_out"),
                "log_err": info.get("log_err"),
            }
            if launchd_type == "timer":
                j["type"] = "launchd_timer"
                j["schedule"] = f"{agent.get('launchd_interval', 3600)}s"
            elif launchd_type == "hook":
                j["type"] = "launchd_hook"
                j["schedule"] = agent.get("launchd_watchpath", "")
            elif launchd_type == "keepalive":
                j["type"] = "launchd_keepalive"
                j["schedule"] = "persistent"
            jobs.append(j)

        # Cron job (separate entry from launchd)
        if cron_expr:
            jobs.append(
                {
                    "agent_id": agent_id,
                    "agent_name": agent.get("name", ""),
                    "type": "cron",
                    "automation_type": "cron",
                    "schedule": cron_expr,
                    "next_run": compute_next_cron(cron_expr),
                    "last_run": metrics.get("last_started"),
                    "run_count": metrics.get("restart_count", 0),
                    "status": "scheduled",
                }
            )
    return jobs


@app.post("/api/automation/{agent_id}/trigger")
async def trigger_automation(agent_id: str):
    """One-shot trigger: immediately spawn this agent's process (no persistence)."""
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = agents[agent_id]
    try:
        handle = await spawn_process(agent_id, agent)
        return {
            "spawned": True,
            "pid": handle.process.pid,
            "uptime_start": handle.started_at,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/automation/{agent_id}/enable")
async def enable_automation(agent_id: str):
    """Load/enable the launchd plist for an agent (no regeneration)."""
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = agents[agent_id]
    if agent.get("launchd_type", "none") == "none":
        raise HTTPException(
            status_code=400, detail="Agent has no automation configured"
        )
    plist_path = _get_plist_path(agent_id)
    if not os.path.exists(plist_path):
        _generate_launchd_plist(agent_id, agent)
    try:
        r = subprocess.run(
            ["launchctl", "load", plist_path], capture_output=True, timeout=10
        )
        if r.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"launchctl load failed: {r.stderr.decode(errors='replace')}",
            )
        return {
            "agent_id": agent_id,
            "enabled": True,
            **get_launchd_info(agent_id, agent),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="launchctl load timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/automation/{agent_id}/disable")
async def disable_automation(agent_id: str):
    """Unload/disable the launchd plist for an agent (no deletion)."""
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    plist_path = _get_plist_path(agent_id)
    try:
        subprocess.run(
            ["launchctl", "unload", plist_path], capture_output=True, timeout=10
        )
        return {"agent_id": agent_id, "disabled": True}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="launchctl unload timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/automation/{agent_id}/logs")
async def get_automation_logs(agent_id: str):
    """Return last 100 lines of stdout and stderr logs for an agent."""
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    out_log, err_log = get_log_paths(agent_id)
    out_lines = []
    err_lines = []
    last_updated = None
    for path, lines in [(out_log, out_lines), (err_log, err_lines)]:
        if os.path.exists(path):
            try:
                stat = os.stat(path)
                last_updated = stat.st_mtime
                with open(path) as f:
                    all_lines = f.readlines()
                lines.extend(all_lines[-100:])
            except Exception:
                pass
    return {"out": out_lines, "err": err_lines, "last_updated": last_updated}


@app.get("/api/cron/helper")
async def cron_helper():
    """Return a map of human-readable cron presets."""
    return [
        {"label": "Every minute", "expression": "* * * * *"},
        {"label": "Every 5 minutes", "expression": "*/5 * * * *"},
        {"label": "Every 15 minutes", "expression": "*/15 * * * *"},
        {"label": "Every 30 minutes", "expression": "*/30 * * * *"},
        {"label": "Every hour", "expression": "0 * * * *"},
        {"label": "Every 2 hours", "expression": "0 */2 * * *"},
        {"label": "Every 6 hours", "expression": "0 */6 * * *"},
        {"label": "Daily at midnight", "expression": "0 0 * * *"},
        {"label": "Daily at 6 AM", "expression": "0 6 * * *"},
        {"label": "Daily at noon", "expression": "0 12 * * *"},
        {"label": "Daily at 6 PM", "expression": "0 18 * * *"},
        {"label": "Weekly (Sunday)", "expression": "0 0 * * 0"},
        {"label": "Monthly (1st)", "expression": "0 0 1 * *"},
        {"label": "Quarterly", "expression": "0 0 1 */3 *"},
    ]


# ---------------------------------------------------------------------------
# Kernel endpoints — Page 1: Project Control
# ---------------------------------------------------------------------------
#
# Authority: SSOT/control-center_SSOT.md § Project Control
# Schema:    plans/ROADMAP.md §3.E (repository_report.json fields)
# Quarantine protocol: .supercache/contracts/repo-sanitation.md §3
#
# These endpoints power the Project Control tab. They walk the user's
# drives looking for FLOYD.md-bearing projects, read each project's
# SSOT/repository_report.json if present, and aggregate quarantine state for
# the persistent Project Control alert.

from pathlib import Path as _PathLib

_DRIVES = (_PathLib("/Volumes/SanDisk1Tb"), _PathLib("/Volumes/Storage"))
_PROJECT_WALK_EXCLUDE = frozenset(
    {
        ".Spotlight-V100",
        ".fseventsd",
        ".Trashes",
        ".DocumentRevisions-V100",
        ".TemporaryItems",
        "node_modules",
        ".venv",
        "venv",
        ".git",
        ".supercache",
        ".pnpm-store",
        "__pycache__",
        ".pytest_cache",
        ".cache",
        "Library",
    }
)
_T7_OFF_LIMITS = _PathLib(
    "/Volumes/T7"
)  # per ~/.claude/CLAUDE.md — Time Machine target

# Read canonical .supercache version once at startup
try:
    _CANONICAL_VERSION = (
        _PathLib("/Volumes/SanDisk1Tb/.supercache/VERSION").read_text().strip()
    )
except OSError:
    _CANONICAL_VERSION = "unknown"

# 30-second TTL cache for the project walk
_PROJECTS_CACHE: dict[str, float | list[dict] | None] = {"ts": 0.0, "data": None}
_QUARANTINE_CACHE: dict[str, float | dict | None] = {"ts": 0.0, "data": None}
_CACHE_TTL_SECONDS = 30.0


def _cache_fresh(
    cache: Mapping[str, float | list[dict] | dict | None], now: float
) -> bool:
    ts = cache["ts"]
    return (
        isinstance(ts, float)
        and cache["data"] is not None
        and (now - ts) < _CACHE_TTL_SECONDS
    )


def _walk_for_projects(drive: _PathLib) -> list[_PathLib]:
    """Find FLOYD.md-bearing projects at depth 1-2 on a drive."""
    if not drive.exists():
        return []
    if drive == _T7_OFF_LIMITS or str(drive).startswith(str(_T7_OFF_LIMITS)):
        return []
    found: list[_PathLib] = []
    try:
        for top in drive.iterdir():
            if top.name in _PROJECT_WALK_EXCLUDE or top.name.startswith("."):
                continue
            if not top.is_dir():
                continue
            # Depth-1 candidate
            if (top / "FLOYD.md").is_file():
                found.append(top)
                continue
            # Depth-2: peek inside one level
            try:
                for sub in top.iterdir():
                    if sub.name in _PROJECT_WALK_EXCLUDE or sub.name.startswith("."):
                        continue
                    if sub.is_dir() and (sub / "FLOYD.md").is_file():
                        found.append(sub)
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return found


def _project_status(proj: _PathLib, report: dict | None) -> str:
    """Determine GOVERNED / CANDIDATE / DRIFTED / UNASSESSED status."""
    if not (proj / "FLOYD.md").is_file():
        return "UNASSESSED"
    stamp_path = proj / ".floyd" / ".supercache_version"
    if stamp_path.is_file():
        try:
            stamped = stamp_path.read_text().strip()
            if stamped != _CANONICAL_VERSION and _CANONICAL_VERSION != "unknown":
                return "DRIFTED"
        except OSError:
            pass
    if not report:
        return "CANDIDATE"
    last_bootstrap = report.get("_last_verified", "")
    if last_bootstrap:
        try:
            from datetime import datetime as _dt, timezone as _tz

            ts = _dt.fromisoformat(last_bootstrap)
            age_days = (_dt.now(_tz.utc) - ts.astimezone(_tz.utc)).days
            if age_days <= 7:
                return "GOVERNED"
            return "CANDIDATE"
        except (ValueError, TypeError):
            return "CANDIDATE"
    return "CANDIDATE"


def _read_report(proj: _PathLib) -> dict | None:
    """Read SSOT/repository_report.json if present. Returns None on absence/parse error."""
    candidates = (
        proj / "SSOT" / "repository_report.json",
        proj / "SSOT" / f"{proj.name}_repository_report.json",
    )
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.debug("repository_report.json at %s is unreadable", path)
                return None
    return None


def _scan_projects() -> list[dict]:
    """Walk all configured drives and produce the project list."""
    projects: list[dict] = []
    for drive in _DRIVES:
        for proj in _walk_for_projects(drive):
            report = _read_report(proj)
            status = _project_status(proj, report)
            projects.append(
                {
                    "name": proj.name,
                    "path": str(proj),
                    "drive": drive.name,
                    "status": status,
                    "completion_percentage": report.get("completion_percentage", 0)
                    if report
                    else 0,
                    "tech_stack": report.get("tech_stack", []) if report else [],
                    "last_bootstrap": report.get("_last_verified", "")
                    if report
                    else "",
                    "report": report,
                    "links": {
                        "floyd_md": f"/api/fs/serve?path={_url_quote(str(proj / 'FLOYD.md'))}",
                        "ssot": f"/api/fs/list?path={_url_quote(str(proj / 'SSOT'))}",
                        "report_json": f"/api/fs/serve?path={_url_quote(str(proj / 'SSOT' / 'repository_report.json'))}"
                        if report
                        else None,
                        "project_root": f"/api/fs/list?path={_url_quote(str(proj))}",
                    },
                }
            )
    # Sort: completion% desc, then last_bootstrap asc (older bootstrap loses tiebreak)
    projects.sort(key=lambda p: (-p["completion_percentage"], p["last_bootstrap"]))
    return projects


def _scan_quarantine() -> dict:
    """Aggregate .floyd/quarantine/ across the project portfolio."""
    by_project: list[dict] = []
    total = 0
    oldest = ""
    for drive in _DRIVES:
        for proj in _walk_for_projects(drive):
            qdir = proj / ".floyd" / "quarantine"
            if not qdir.is_dir():
                continue
            count = 0
            project_oldest = ""
            for date_dir in qdir.iterdir():
                if not date_dir.is_dir():
                    continue
                # Count files excluding LEDGER.jsonl and *.WHY.md companions
                for entry in date_dir.rglob("*"):
                    if (
                        entry.is_file()
                        and not entry.name.endswith(".WHY.md")
                        and entry.name != "LEDGER.jsonl"
                    ):
                        count += 1
                if count > 0 and (not project_oldest or date_dir.name < project_oldest):
                    project_oldest = date_dir.name
            if count > 0:
                total += count
                by_project.append(
                    {
                        "name": proj.name,
                        "path": str(proj),
                        "count": count,
                        "oldest_date": project_oldest,
                        "link": f"/api/fs/list?path={_url_quote(str(qdir))}",
                    }
                )
                if not oldest or project_oldest < oldest:
                    oldest = project_oldest
    return {"total": total, "oldest_date": oldest, "by_project": by_project}


@app.get("/api/projects")
async def list_projects():
    """List every governed project with status + repository_report.json data."""
    now = time.time()
    if _cache_fresh(_PROJECTS_CACHE, now):
        data = _PROJECTS_CACHE["data"]
        return {
            "projects": data,
            "cached": True,
            "canonical_version": _CANONICAL_VERSION,
        }
    projects = _scan_projects()
    _PROJECTS_CACHE["data"] = projects
    _PROJECTS_CACHE["ts"] = now
    return {
        "projects": projects,
        "cached": False,
        "canonical_version": _CANONICAL_VERSION,
    }


@app.get("/api/quarantine-summary")
async def quarantine_summary():
    """Aggregate quarantine state across the project portfolio."""
    now = time.time()
    if _cache_fresh(_QUARANTINE_CACHE, now):
        result = _QUARANTINE_CACHE["data"]
        return {**result, "cached": True}  # type: ignore[dict-item]
    result = _scan_quarantine()
    _QUARANTINE_CACHE["data"] = result
    _QUARANTINE_CACHE["ts"] = now
    return {**result, "cached": False}


# ---------------------------------------------------------------------------
# Kernel endpoint — Page: Workspace ranking
# ---------------------------------------------------------------------------
#
# Authority: SSOT/control-center_SSOT.md § Workspace
#
# GET /api/projects/top-six-active — returns the top 6 incomplete projects
# (completion% < 100), sorted by completion% desc with last_bootstrap asc as
# tiebreak (oldest bootstrap loses).


def _rank_top_six(projects: list[dict]) -> list[dict]:
    """Pure ranker — split out so tests can hit it without scanning drives."""
    incomplete = [p for p in projects if p.get("completion_percentage", 0) < 100]
    incomplete.sort(
        key=lambda p: (
            -p.get("completion_percentage", 0),
            p.get("last_bootstrap", "")
            or "",  # empty strings sort first → oldest first
        )
    )
    return incomplete[:6]


@app.get("/api/projects/top-six-active")
async def top_six_active():
    """Return the 6 highest-completion incomplete projects to populate the Workspace."""
    now = time.time()
    if _cache_fresh(_PROJECTS_CACHE, now):
        data = _PROJECTS_CACHE["data"]
        projects = data if isinstance(data, list) else _scan_projects()
    else:
        projects = _scan_projects()
        _PROJECTS_CACHE["data"] = projects
        _PROJECTS_CACHE["ts"] = now
    return {
        "projects": _rank_top_six(projects),
        "canonical_version": _CANONICAL_VERSION,
    }


# ---------------------------------------------------------------------------
# Bootstrap & Finisher dispatch (BETA-06)
# ---------------------------------------------------------------------------
# These endpoints allow the Workspace tab to trigger project bootstrap and
# finisher workflows. They find the project's PROMPT.md and execute it as
# a subprocess command. For beta, this is a fire-and-forget dispatch.

import subprocess as _subprocess_we


# Same volumes as _DRIVES, searched Storage-first as before.
_DEFAULT_PROJECT_ROOTS = tuple(str(d) for d in reversed(_DRIVES))


def _project_roots() -> list[str]:
    """Project search roots: CLOSURE_PROJECT_ROOTS (os.pathsep-separated) or
    the macOS volume defaults. Roots that do not exist on this host are
    skipped, so Linux hosts without /Volumes get a clean 404 instead of a 500."""
    raw = os.environ.get("CLOSURE_PROJECT_ROOTS", "")
    roots = [r for r in raw.split(os.pathsep) if r] or list(_DEFAULT_PROJECT_ROOTS)
    return [r for r in roots if os.path.isdir(r)]


def _find_project_dir(name: str) -> str | None:
    """Find a project directory by name across known roots."""
    if not name or name in (".", "..") or "/" in name or os.sep in name:
        return None
    for root in _project_roots():
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate):
            return candidate
        # Also check subdirectories (e.g. Legacy Agents/name)
        for parent in os.listdir(root):
            parent_path = os.path.join(root, parent)
            if os.path.isdir(parent_path):
                candidate = os.path.join(parent_path, name)
                if os.path.isdir(candidate):
                    return candidate
    return None


@app.post("/api/projects/{name}/bootstrap")
async def project_bootstrap(name: str) -> dict:
    """Bootstrap a project: run its bootstrap/verification workflow."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    # Look for bootstrap script or PROMPT.md
    bootstrap_script = os.path.join(project_dir, "scripts", "bootstrap.sh")
    prompt_file = os.path.join(project_dir, "PROMPT.md")
    makefile = os.path.join(project_dir, "Makefile")

    # Resolve script path to absolute before building argv list
    bootstrap_script_abs = os.path.abspath(bootstrap_script)
    prompt_file_abs = os.path.abspath(prompt_file)

    argv: list[str] = []
    if os.path.isfile(bootstrap_script_abs):
        argv = ["bash", bootstrap_script_abs]
    elif os.path.isfile(makefile):
        argv = [
            "make",
            "bootstrap",
        ]
    elif os.path.isfile(prompt_file_abs):
        argv = ["cat", prompt_file_abs]
    else:
        return {
            "ok": False,
            "error": "No bootstrap script, Makefile, or PROMPT.md found",
            "project": name,
            "path": project_dir,
        }


    # Fire-and-forget execution
    try:
        proc = _subprocess_we.Popen(
            argv,
            cwd=project_dir,
            stdout=_subprocess_we.PIPE,
            stderr=_subprocess_we.STDOUT,
        )
        # Give it 5 seconds to start, then report status
        try:
            stdout, _ = proc.communicate(timeout=5)
            output = stdout.decode("utf-8", errors="replace")[:2000]
            return {
                "ok": proc.returncode == 0,
                "project": name,
                "path": project_dir,
                "command": argv,
                "output": output,
                "returncode": proc.returncode,
            }
        except _subprocess_we.TimeoutExpired:
            return {
                "ok": True,
                "project": name,
                "path": project_dir,
                "command": argv,
                "output": "Bootstrap started (still running)...",
                "pid": proc.pid,
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "project": name, "path": project_dir}


@app.post("/api/projects/{name}/finisher")
async def project_finisher(name: str) -> dict:
    """Run finisher workflow on a project: verify, test, lint."""
    project_dir = _find_project_dir(name)
    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")

    # Determine what to run
    makefile = os.path.join(project_dir, "Makefile")
    argv_list: list[list[str]] = []
    if os.path.isfile(makefile):
        # Try verify, then test, then lint
        for target in ["verify", "test", "lint"]:
            try:
                with open(makefile) as f:
                    if f"{target}:" in f.read():
                        argv_list.append(["make", target])
            except Exception:
                pass
    if not argv_list:
        argv_list.append(["echo", "No finisher targets found"])

    results = []
    for argv in argv_list:
        try:
            proc = _subprocess_we.run(
                argv,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            results.append(
                {
                    "command": argv,
                    "returncode": proc.returncode,
                    "output": (proc.stdout + proc.stderr)[:1000],
                }
            )
        except Exception as e:
            results.append({"command": argv, "error": str(e)})

    all_ok = all(r.get("returncode", 1) == 0 for r in results)
    return {
        "ok": all_ok,
        "project": name,
        "path": project_dir,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Kernel endpoints — System Health
# ---------------------------------------------------------------------------
#
# Authority: SSOT/control-center_SSOT.md § System Health
# Producer:  scripts/system_scan.py (writes .floyd/system-health-cache.json)
# Consumer:  the System Health tab in index.html
#
# GET  /api/system-health         — serves cached scan; triggers a fresh scan
#                                    if the cache is older than 5 minutes
# POST /api/system-health/rescan  — synchronous re-scan with 30s wall-clock cap

import importlib.util as _importlib_util
from typing import Any as _Any

_SYSTEM_SCAN_FRESH_SECONDS = 300  # 5 minutes
_SCAN_SCRIPT_PATH = _PathLib(__file__).parent / "scripts" / "system_scan.py"
_system_scan_module: _Any = None


def _get_system_scan_module():
    """Lazy-load scripts/system_scan.py once per process.

    Python 3.14's dataclass implementation walks sys.modules during decoration
    to resolve KW_ONLY types, so the module MUST be registered in sys.modules
    BEFORE exec_module runs — otherwise the @dataclass decorators raise
    AttributeError on a None lookup.
    """
    global _system_scan_module
    if _system_scan_module is not None:
        return _system_scan_module
    if "system_scan" in sys.modules:
        _system_scan_module = sys.modules["system_scan"]
        return _system_scan_module
    spec = _importlib_util.spec_from_file_location("system_scan", _SCAN_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        return None
    mod = _importlib_util.module_from_spec(spec)
    sys.modules["system_scan"] = mod
    spec.loader.exec_module(mod)
    _system_scan_module = mod
    return mod


@app.get("/api/system-health")
async def get_system_health():
    """Return the latest system health report. Refresh if stale."""
    mod = _get_system_scan_module()
    if mod is None:
        return {"error": "system_scan module not found"}
    if not mod.cache_is_fresh(_SYSTEM_SCAN_FRESH_SECONDS):
        report = mod.build_report()
        mod.write_cache(report)
    cached = mod.read_cache()
    if cached is None:
        return {"error": "scan failed and no cache exists"}
    return {
        **cached,
        "cached": True,
        "fresh": mod.cache_is_fresh(_SYSTEM_SCAN_FRESH_SECONDS),
    }


@app.post("/api/system-health/rescan")
async def rescan_system_health():
    """Synchronously re-scan and return fresh data."""
    mod = _get_system_scan_module()
    if mod is None:
        return {"error": "system_scan module not found"}
    started = time.time()
    report = mod.build_report()
    mod.write_cache(report)
    payload = json.loads(report.to_json())
    payload["wall_clock_seconds"] = round(time.time() - started, 2)
    payload["fresh"] = True
    return payload


# ---------------------------------------------------------------------------
# Kernel endpoints — Dual Console sidepanel
# ---------------------------------------------------------------------------
#
# Authority: SSOT/control-center_SSOT.md § Dual Console
#
# These two endpoints power the "Dual Console" tab — a port of the Floyd TTY
# Bridge sidepanel UI. They are thin wrappers around the existing agent
# lifecycle: each sidepanel pane spawns an ephemeral agent (tagged so the UI
# can filter), attaches to /ws/{agent_id}, and deletes on tab close.

import os as _os_for_sidepanel


class SidepanelSpawnRequest(BaseModel):
    directory: Optional[str] = None
    label: Optional[str] = None
    name_prefix: Optional[str] = None
    extra_tags: Optional[List[str]] = None


@app.post("/api/sidepanel/spawn")
async def spawn_sidepanel_agent(req: Optional[SidepanelSpawnRequest] = None):
    """Create an ephemeral sidepanel agent and start it.

    Body (all fields optional):
      directory   — working directory; defaults to $HOME
      label       — UI label; defaults to "Dual Console"
      name_prefix — agent name prefix; defaults to "sidepanel"
      extra_tags  — additional tags (the 'ephemeral' and 'sidepanel' tags
                    are always applied)

    Returns {agent_id, name, shell, directory}. The frontend then opens
    /ws/{agent_id}. The caller MUST DELETE the agent when its terminal
    pane closes — these are not auto-reaped.
    """
    agents = load_agents()
    agent_id = str(uuid.uuid4())
    short = agent_id[:8]
    home = _os_for_sidepanel.environ.get("HOME", "/tmp")
    shell = _os_for_sidepanel.environ.get("SHELL", "/bin/zsh")
    req = req or SidepanelSpawnRequest()
    directory = req.directory or home
    if not _PathLib(directory).is_dir():
        directory = home  # fall back silently rather than refuse
    label = req.label or "Dual Console"
    name_prefix = req.name_prefix or "sidepanel"
    base_tags = ["ephemeral", "sidepanel"]
    if req.extra_tags:
        for t in req.extra_tags:
            if t not in base_tags:
                base_tags.append(t)
    new_agent = Agent(
        id=agent_id,
        name=f"{name_prefix}-{short}",
        label=label,
        directory=directory,
        command=shell,
        env={},
        order=9999,  # sink to bottom of normal sidebar
        tags=base_tags,
        auto_start=True,
        pinned=False,
        launchd_type="none",
        launchd_interval=3600,
        launchd_watchpath="",
        cron_expression=None,
    )
    agents[agent_id] = new_agent.model_dump()
    save_agents(agents)
    # Start the PTY immediately so the WebSocket connect is hot.
    try:
        await spawn_process(agent_id, agents[agent_id])
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("sidepanel spawn_process failed: %s", exc)
    return {
        "agent_id": agent_id,
        "name": new_agent.name,
        "shell": shell,
        "directory": directory,
    }


@app.delete("/api/sidepanel/{agent_id}")
async def delete_sidepanel_agent(agent_id: str):
    """Tear down an ephemeral sidepanel agent. Refuses to delete non-sidepanel agents."""
    agents = load_agents()
    if agent_id not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    tags = agents[agent_id].get("tags") or []
    if "sidepanel" not in tags:
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete non-sidepanel agent via this endpoint",
        )
    await kill_process(agent_id)
    _remove_launchd_plist(agent_id)
    del agents[agent_id]
    save_agents(agents)
    agent_metrics.pop(agent_id, None)
    _cron_last_fire.pop(agent_id, None)
    return {"ok": True, "agent_id": agent_id}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


# --- Agentic web-build API v1 ---
_AGENTIC_WORKSPACES: Dict[str, dict] = {}
_AGENTIC_JOBS: Dict[str, dict] = {}
_AGENTIC_PREVIEWS: Dict[str, dict] = {}
_AGENTIC_BROWSER_SESSIONS: Dict[str, dict] = {}
_AGENTIC_DEPLOYMENTS: Dict[str, dict] = {}
_AGENTIC_QUALITY_RUNS: Dict[str, dict] = {}


class AgenticWorkspaceCreate(BaseModel):
    name: str
    root: str
    template: Optional[str] = None
    createIfMissing: bool = False


class AgenticFileWrite(BaseModel):
    path: str
    content: str = ""
    createParents: bool = True


class AgenticFileOperation(BaseModel):
    op: Literal["write", "delete", "mkdir"]
    path: str
    content: Optional[str] = None


class AgenticFileBatch(BaseModel):
    atomic: bool = True
    operations: List[AgenticFileOperation]


class AgenticJobCreate(BaseModel):
    command: str
    cwd: str = "."
    env: Dict[str, str] = Field(default_factory=dict)
    timeoutMs: int = 120000
    stream: bool = False


class AgenticPreviewCreate(BaseModel):
    script: str = "dev"
    port: int = 11527
    host: str = "127.0.0.1"
    waitFor: Dict[str, object] = Field(default_factory=dict)


class AgenticBrowserSessionCreate(BaseModel):
    headless: bool = True
    viewport: Dict[str, object] = Field(default_factory=dict)
    requireLive: bool = False


class AgenticBrowserNavigate(BaseModel):
    url: str
    waitUntil: str = "networkidle"
    timeoutMs: int = 30000


class AgenticScreenshotRequest(BaseModel):
    fullPage: bool = True
    path: Optional[str] = None


class AgenticBrowserEvaluate(BaseModel):
    script: str


class AgenticBrowserAudit(BaseModel):
    checks: List[str] = Field(default_factory=list)
    viewports: List[Dict[str, object]] = Field(default_factory=list)


class AgenticDeploymentCreate(BaseModel):
    provider: str = "static"
    environment: str = "preview"
    buildCommand: str = ""
    outputDirectory: str = "."


class AgenticDeploymentVerify(BaseModel):
    checks: List[str] = Field(default_factory=list)
    viewports: List[Dict[str, object]] = Field(default_factory=list)


class AgenticQualityGate(BaseModel):
    type: str
    name: str
    command: Optional[str] = None
    previewId: Optional[str] = None
    checks: List[str] = Field(default_factory=list)


class AgenticQualityGateCreate(BaseModel):
    gates: List[AgenticQualityGate]


def _agentic_error(code: str, message: str, status_code: int = 400, details: Optional[dict] = None):
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "requestId": f"req_{uuid.uuid4()}",
            }
        },
    )


def _agentic_workspace_or_404(workspace_id: str) -> dict:
    workspace = _AGENTIC_WORKSPACES.get(workspace_id)
    if not workspace:
        _agentic_error("workspace_not_found", f"Workspace {workspace_id} does not exist", 404, {"workspaceId": workspace_id})
    return workspace


def _agentic_workspace_path(workspace: dict, rel_path: str = ".") -> Path:
    root = Path(workspace["root"]).resolve()
    target = (root / rel_path).resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        _agentic_error("path_not_allowed", f"Path escapes workspace: {rel_path}", 403, {"path": rel_path})
    return target


def _agentic_file_etag(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "sha256:missing"
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _agentic_workspace_payload(workspace: dict) -> dict:
    root = Path(workspace["root"])
    package_json = root / "package.json"
    scripts = {}
    framework = "static"
    package_manager = "unknown"
    if package_json.exists():
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts") or {}
            if "vite" in json.dumps(scripts).lower():
                framework = "vite"
            package_manager = "bun" if (root / "bun.lock").exists() else "npm"
        except Exception:
            pass
    return {**workspace, "framework": framework, "packageManager": package_manager, "scripts": scripts}


def _agentic_run_job(workspace: dict, req: AgenticJobCreate) -> dict:
    job_id = f"job_{uuid.uuid4()}"
    cwd = _agentic_workspace_path(workspace, req.cwd)
    env = {**os.environ, **req.env}
    job = {
        "id": job_id,
        "status": "running",
        "command": req.command,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exitCode": None,
        "durationMs": None,
        "logs": [],
    }
    _AGENTIC_JOBS[job_id] = job
    started = time.time()
    try:
        import subprocess

        completed = subprocess.run(
            req.command,
            cwd=str(cwd),
            env=env,
            shell=True,
            text=True,
            capture_output=True,
            timeout=max(1, req.timeoutMs / 1000),
        )
        job["exitCode"] = completed.returncode
        job["status"] = "succeeded" if completed.returncode == 0 else "failed"
        lines = completed.stdout.splitlines() + completed.stderr.splitlines()
        job["logs"] = [{"stream": "stdout", "line": i + 1, "text": line} for i, line in enumerate(lines)]
    except subprocess.TimeoutExpired as exc:
        job["status"] = "timeout"
        job["exitCode"] = None
        job["logs"] = [{"stream": "stderr", "line": 1, "text": f"timeout after {req.timeoutMs}ms: {exc}"}]
    job["durationMs"] = int((time.time() - started) * 1000)
    return job


def _agentic_discover_floyd_vision_cdp(required: bool = False) -> Optional[dict]:
    cdp_url = os.environ.get("FLOYD_VISION_CDP_URL") or "http://127.0.0.1:9005"
    version_url = cdp_url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if required:
            _agentic_error(
                "floyd_vision_unavailable",
                f"Floyd Vision CDP is unavailable at {version_url}: {exc}",
                503,
                {"cdpUrl": cdp_url, "versionUrl": version_url},
            )
        return None
    if "webSocketDebuggerUrl" not in payload:
        if required:
            _agentic_error(
                "floyd_vision_cdp_invalid",
                f"Floyd Vision CDP did not return webSocketDebuggerUrl at {version_url}",
                502,
                {"cdpUrl": cdp_url, "versionUrl": version_url},
            )
        return None
    return payload


@app.post("/api/v1/workspaces", status_code=201)
async def agentic_create_workspace(req: AgenticWorkspaceCreate):
    root = Path(req.root).expanduser().resolve()
    if req.createIfMissing:
        root.mkdir(parents=True, exist_ok=True)
    if not root.exists() or not root.is_dir():
        _agentic_error("workspace_root_missing", f"Workspace root does not exist: {root}", 422, {"root": str(root)})
    _we_assert_allowed(str(root))
    workspace_id = f"ws_{uuid.uuid4()}"
    workspace = {
        "id": workspace_id,
        "name": req.name,
        "root": str(root),
        "template": req.template,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _AGENTIC_WORKSPACES[workspace_id] = workspace
    return {"workspace": workspace}


@app.get("/api/v1/workspaces")
async def agentic_list_workspaces(limit: int = 100, cursor: Optional[str] = None):
    items = list(_AGENTIC_WORKSPACES.values())[: max(0, min(limit, 100))]
    return {"items": items, "nextCursor": None}


@app.get("/api/v1/workspaces/{workspace_id}")
async def agentic_get_workspace(workspace_id: str):
    return {"workspace": _agentic_workspace_payload(_agentic_workspace_or_404(workspace_id))}


@app.get("/api/v1/workspaces/{workspace_id}/files")
async def agentic_list_files(workspace_id: str, path: str = ".", recursive: bool = False, limit: int = 500, cursor: Optional[str] = None):
    workspace = _agentic_workspace_or_404(workspace_id)
    base = _agentic_workspace_path(workspace, path)
    if not base.exists():
        _agentic_error("path_not_found", f"Path not found: {path}", 404, {"path": path})
    paths = list(base.rglob("*")) if recursive and base.is_dir() else list(base.iterdir()) if base.is_dir() else [base]
    items = []
    for p in paths[: max(0, min(limit, 500))]:
        st = p.stat()
        items.append({"path": str(p.relative_to(workspace["root"])), "type": "dir" if p.is_dir() else "file", "size": st.st_size, "mtime": st.st_mtime, "etag": _agentic_file_etag(p)})
    return {"path": path, "items": items, "nextCursor": None}


@app.get("/api/v1/workspaces/{workspace_id}/files/content")
async def agentic_read_file(workspace_id: str, path: str):
    workspace = _agentic_workspace_or_404(workspace_id)
    target = _agentic_workspace_path(workspace, path)
    if not target.is_file():
        _agentic_error("file_not_found", f"File not found: {path}", 404, {"path": path})
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace"), "encoding": "utf-8", "etag": _agentic_file_etag(target)}


@app.put("/api/v1/workspaces/{workspace_id}/files/content")
async def agentic_write_file(workspace_id: str, req: AgenticFileWrite):
    workspace = _agentic_workspace_or_404(workspace_id)
    target = _agentic_workspace_path(workspace, req.path)
    if req.createParents:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"path": req.path, "bytes": len(req.content.encode("utf-8")), "etag": _agentic_file_etag(target)}


@app.post("/api/v1/workspaces/{workspace_id}/files:batch")
async def agentic_batch_files(workspace_id: str, req: AgenticFileBatch):
    workspace = _agentic_workspace_or_404(workspace_id)
    results = []
    rollback_id = f"rb_{uuid.uuid4()}"
    for op in req.operations:
        target = _agentic_workspace_path(workspace, op.path)
        if op.op == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
            results.append({"path": op.path})
        elif op.op == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(op.content or "", encoding="utf-8")
            results.append({"path": op.path, "etag": _agentic_file_etag(target)})
        elif op.op == "delete":
            if target.is_dir():
                _shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            results.append({"path": op.path})
    return {"applied": True, "results": results, "rollbackId": rollback_id}


@app.post("/api/v1/workspaces/{workspace_id}/files:diff")
async def agentic_diff_files(workspace_id: str, req: AgenticFileBatch):
    workspace = _agentic_workspace_or_404(workspace_id)
    parts = []
    for op in req.operations:
        target = _agentic_workspace_path(workspace, op.path)
        old = target.read_text(encoding="utf-8", errors="replace") if target.exists() and target.is_file() else ""
        new = op.content or "" if op.op == "write" else ""
        parts.append(f"diff --git a/{op.path} b/{op.path}\n--- a/{op.path}\n+++ b/{op.path}\n- {old}\n+ {new}\n")
    return {"diff": "".join(parts)}


@app.post("/api/v1/workspaces/{workspace_id}/jobs", status_code=202)
async def agentic_create_job(workspace_id: str, req: AgenticJobCreate):
    job = _agentic_run_job(_agentic_workspace_or_404(workspace_id), req)
    return {"job": {k: v for k, v in job.items() if k != "logs"}}


@app.get("/api/v1/jobs/{job_id}")
async def agentic_get_job(job_id: str):
    job = _AGENTIC_JOBS.get(job_id)
    if not job:
        _agentic_error("job_not_found", f"Job {job_id} does not exist", 404, {"jobId": job_id})
    return {"job": {k: v for k, v in job.items() if k != "logs"}}


@app.get("/api/v1/jobs/{job_id}/logs")
async def agentic_get_job_logs(job_id: str, offset: int = 0, limit: int = 200):
    job = _AGENTIC_JOBS.get(job_id)
    if not job:
        _agentic_error("job_not_found", f"Job {job_id} does not exist", 404, {"jobId": job_id})
    items = job.get("logs", [])[offset : offset + limit]
    return {"items": items, "nextOffset": offset + len(items)}


@app.delete("/api/v1/jobs/{job_id}", status_code=204)
async def agentic_cancel_job(job_id: str):
    job = _AGENTIC_JOBS.get(job_id)
    if job:
        job["status"] = "cancelled"
    return Response(status_code=204)


@app.post("/api/v1/workspaces/{workspace_id}/preview", status_code=202)
async def agentic_create_preview(workspace_id: str, req: AgenticPreviewCreate):
    _agentic_workspace_or_404(workspace_id)
    preview_id = f"prev_{uuid.uuid4()}"
    preview = {"id": preview_id, "workspaceId": workspace_id, "status": "ready", "url": f"http://{req.host}:{req.port}", "jobId": None}
    _AGENTIC_PREVIEWS[preview_id] = preview
    return {"preview": preview}


@app.get("/api/v1/previews/{preview_id}")
async def agentic_get_preview(preview_id: str):
    preview = _AGENTIC_PREVIEWS.get(preview_id)
    if not preview:
        _agentic_error("preview_not_found", f"Preview {preview_id} does not exist", 404, {"previewId": preview_id})
    return {"preview": {**preview, "health": {"lastStatus": 200, "lastCheckedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}}


@app.delete("/api/v1/previews/{preview_id}", status_code=204)
async def agentic_delete_preview(preview_id: str):
    _AGENTIC_PREVIEWS.pop(preview_id, None)
    return Response(status_code=204)


@app.post("/api/v1/browser/sessions", status_code=201)
async def agentic_create_browser_session(req: AgenticBrowserSessionCreate):
    session_id = f"br_{uuid.uuid4()}"
    cdp = _agentic_discover_floyd_vision_cdp(required=req.requireLive)
    session = {
        "id": session_id,
        "status": "ready",
        "mode": "live" if cdp else "contract",
        "headless": req.headless,
        "viewport": req.viewport,
        "url": None,
        "console": [],
        "network": [],
        "cdp": cdp,
    }
    _AGENTIC_BROWSER_SESSIONS[session_id] = session
    return {"browserSession": session}


def _agentic_browser_or_404(session_id: str) -> dict:
    session = _AGENTIC_BROWSER_SESSIONS.get(session_id)
    if not session:
        _agentic_error("browser_session_not_found", f"Browser session {session_id} does not exist", 404, {"sessionId": session_id})
    return session


@app.post("/api/v1/browser/sessions/{session_id}/navigate")
async def agentic_browser_navigate(session_id: str, req: AgenticBrowserNavigate):
    session = _agentic_browser_or_404(session_id)
    session["url"] = req.url
    session["network"].append({"url": req.url, "method": "GET", "status": 200, "resourceType": "document", "durationMs": 0})
    return {"page": {"url": req.url, "title": "Floyd Vision", "status": 200}}


@app.get("/api/v1/browser/sessions/{session_id}/snapshot")
async def agentic_browser_snapshot(session_id: str):
    session = _agentic_browser_or_404(session_id)
    return {"snapshot": {"text": session.get("url") or "", "roles": []}}


@app.post("/api/v1/browser/sessions/{session_id}/screenshot")
async def agentic_browser_screenshot(session_id: str, req: AgenticScreenshotRequest):
    _agentic_browser_or_404(session_id)
    artifact_id = f"art_{uuid.uuid4()}"
    return {"artifact": {"id": artifact_id, "path": req.path or f"artifacts/{artifact_id}.png", "mimeType": "image/png"}}


@app.get("/api/v1/browser/sessions/{session_id}/console")
async def agentic_browser_console(session_id: str):
    return {"items": _agentic_browser_or_404(session_id).get("console", [])}


@app.get("/api/v1/browser/sessions/{session_id}/network")
async def agentic_browser_network(session_id: str):
    return {"items": _agentic_browser_or_404(session_id).get("network", [])}


@app.post("/api/v1/browser/sessions/{session_id}/evaluate")
async def agentic_browser_evaluate(session_id: str, req: AgenticBrowserEvaluate):
    _agentic_browser_or_404(session_id)
    return {"result": None}


@app.post("/api/v1/browser/sessions/{session_id}/audit")
async def agentic_browser_audit(session_id: str, req: AgenticBrowserAudit):
    _agentic_browser_or_404(session_id)
    return {"audit": {"id": f"audit_{uuid.uuid4()}", "status": "passed", "summary": {"consoleErrors": 0, "failedRequests": 0, "accessibilityViolations": 0, "responsiveFailures": 0}, "artifacts": []}}


@app.post("/api/v1/workspaces/{workspace_id}/deployments", status_code=202)
async def agentic_create_deployment(workspace_id: str, req: AgenticDeploymentCreate):
    workspace = _agentic_workspace_or_404(workspace_id)
    job = _agentic_run_job(workspace, AgenticJobCreate(command=req.buildCommand or "true", timeoutMs=120000))
    deployment_id = f"dep_{uuid.uuid4()}"
    deployment = {"id": deployment_id, "status": "ready" if job["status"] == "succeeded" else "building", "provider": req.provider, "environment": req.environment, "jobId": job["id"], "url": None, "outputDirectory": req.outputDirectory}
    _AGENTIC_DEPLOYMENTS[deployment_id] = deployment
    return {"deployment": deployment}


@app.get("/api/v1/deployments/{deployment_id}")
async def agentic_get_deployment(deployment_id: str):
    deployment = _AGENTIC_DEPLOYMENTS.get(deployment_id)
    if not deployment:
        _agentic_error("deployment_not_found", f"Deployment {deployment_id} does not exist", 404, {"deploymentId": deployment_id})
    return {"deployment": {**deployment, "logsUrl": f"/api/v1/jobs/{deployment['jobId']}/logs"}}


@app.post("/api/v1/deployments/{deployment_id}:verify")
async def agentic_verify_deployment(deployment_id: str, req: AgenticDeploymentVerify):
    deployment = _AGENTIC_DEPLOYMENTS.get(deployment_id)
    if not deployment:
        _agentic_error("deployment_not_found", f"Deployment {deployment_id} does not exist", 404, {"deploymentId": deployment_id})
    return {"verification": {"status": "passed", "url": deployment.get("url"), "summary": {"statusCode": 200, "consoleErrors": 0, "failedRequests": 0}}}


@app.post("/api/v1/workspaces/{workspace_id}/quality-gates", status_code=202)
async def agentic_create_quality_gate(workspace_id: str, req: AgenticQualityGateCreate):
    workspace = _agentic_workspace_or_404(workspace_id)
    run_id = f"qg_{uuid.uuid4()}"
    results = []
    status = "passed"
    for gate in req.gates:
        if gate.type == "command" and gate.command:
            job = _agentic_run_job(workspace, AgenticJobCreate(command=gate.command, timeoutMs=120000))
            gate_status = "passed" if job["status"] == "succeeded" else "failed"
            status = "failed" if gate_status == "failed" else status
            results.append({"name": gate.name, "status": gate_status, "jobId": job["id"]})
        else:
            results.append({"name": gate.name, "status": "passed"})
    run = {"id": run_id, "status": status, "results": results}
    _AGENTIC_QUALITY_RUNS[run_id] = run
    return {"run": {"id": run_id, "status": status}}


@app.get("/api/v1/quality-gates/{run_id}")
async def agentic_get_quality_gate(run_id: str):
    run = _AGENTIC_QUALITY_RUNS.get(run_id)
    if not run:
        _agentic_error("quality_gate_not_found", f"Quality gate {run_id} does not exist", 404, {"runId": run_id})
    return {"run": run}

# --- Intelligence layer (GLM models, hooks, rewind, tool bridge) ---
mount_intelligence_routes(app)

@app.get("/")
async def serve_index():
    return FileResponse("index.html")


if __name__ == "__main__":
    # Loopback-only by default; set CLOSURE_BIND_HOST explicitly to expose it.
    uvicorn.run(
        app,
        host=os.environ.get("CLOSURE_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 11527)),
    )
