"""Shared helpers for TCC tests: server-lifecycle, polling, artifact paths."""

from __future__ import annotations

import os
import pathlib
import subprocess
import time
from typing import Optional

import httpx


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = REPO_ROOT / "tests" / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_BASE = os.environ.get("TCC_BASE", "http://localhost:11527")
LAUNCHD_LABEL = "com.legacyai.tcc"


def base_url() -> str:
    return DEFAULT_BASE


def is_server_up(timeout: float = 1.5) -> bool:
    try:
        r = httpx.get(f"{DEFAULT_BASE}/api/health", timeout=timeout)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


def wait_for_server(timeout: float = 30.0, poll: float = 0.4) -> bool:
    """Block until /api/health responds or timeout. Returns True on success."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_server_up():
            return True
        time.sleep(poll)
    return False


def restart_launchd_tcc(timeout: float = 30.0) -> bool:
    """Cleanly bounce the launchd-managed TCC.

    Uses `launchctl kickstart -k` which kills the running instance and relaunches it from the
    same plist. Returns True once /api/health is up again.
    """
    uid = os.getuid()
    target = f"gui/{uid}/{LAUNCHD_LABEL}"
    subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        check=False,
        timeout=10,
    )
    return wait_for_server(timeout=timeout)


def list_agents() -> list[dict]:
    r = httpx.get(f"{DEFAULT_BASE}/api/agents", timeout=5)
    r.raise_for_status()
    return r.json()


def find_floyd_agent(preferred: str = "FLOYD-STABILITY") -> Optional[dict]:
    """Return the agent dict for the named floyd agent (or first floyd-tagged one)."""
    agents = list_agents()
    for a in agents:
        if a.get("name") == preferred:
            return a
    for a in agents:
        if "floyd" in (a.get("tags") or []):
            return a
    return None


def run_llm_do(action: str, **kwargs) -> dict:
    """Convenience wrapper for the LLM API endpoint."""
    payload = {"action": action, **{k: v for k, v in kwargs.items() if v is not None}}
    r = httpx.post(f"{DEFAULT_BASE}/api/llm/do", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def kill_agent_processes(agent_name_substring: str) -> int:
    """Stop any running TCC-managed processes whose agent name contains the substring.

    Uses /api/llm/do action=stop for graceful shutdown. Returns count stopped.
    """
    stopped = 0
    for a in list_agents():
        if agent_name_substring.lower() in a.get("name", "").lower():
            if a.get("status") == "running":
                try:
                    run_llm_do("stop", agent=a["name"])
                    stopped += 1
                except Exception:
                    pass
    return stopped

