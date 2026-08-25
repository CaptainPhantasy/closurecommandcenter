"""End-to-end automation tests — CRON scheduling and launchd integration.

These tests exercise the same paths the daily user does:
- Set a cron expression on an agent → background scheduler fires it → process appears live in TCC.
- Set launchd_type=timer on an agent → plist is generated, loaded by launchctl, observable via the
  automation/jobs endpoint, and visible to `launchctl list`.
- Disable / re-enable the launchd plist via the automation API.

The CRON test is real-time: it creates an agent with `* * * * *` (every minute), waits up to 75
seconds, and verifies the scheduler fired it. To skip the slow CRON test:
    SKIP_SLOW_CRON=1 pytest tests/test_automation_e2e.py -v
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.live_server

from _helpers import base_url


def _client():
    return httpx.Client(base_url=base_url(), timeout=15.0)


def _create_agent(c: httpx.Client, **kwargs) -> dict:
    name = kwargs.pop("name", f"auto-{uuid.uuid4().hex[:8]}")
    body = {
        "name": name,
        "directory": "/tmp",
        "command": "bash -lc 'echo automation_test_$$; sleep 30'",
        "tags": ["automation-test"],
        **kwargs,
    }
    r = c.post("/api/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _delete_agent(c: httpx.Client, agent_id: str) -> None:
    try:
        c.delete(f"/api/agents/{agent_id}")
    except Exception:
        pass


def _plist_path(agent_id: str) -> str:
    return os.path.expanduser(
        f"~/Library/LaunchAgents/com.legacyai.tcc.{agent_id}.plist"
    )


# ---- launchd integration ---------------------------------------------------


def test_launchd_timer_creates_loadable_plist():
    """Workflow: user creates an agent with launchd_type=timer → expects a plist
    on disk, loaded into launchctl, and surfaced by /api/automation/jobs."""
    with _client() as c:
        agent = _create_agent(
            c,
            launchd_type="timer",
            launchd_interval=86400,  # 1/day — long enough not to fire during this test
        )
        agent_id = agent["id"]
        try:
            # 1. Plist file exists on disk
            plist = _plist_path(agent_id)
            assert os.path.exists(plist), f"plist not created: {plist}"
            content = open(plist).read()
            assert "<key>StartInterval</key>" in content
            assert "<integer>86400</integer>" in content
            assert f"com.legacyai.tcc.{agent_id}" in content

            # 2. launchctl knows about it
            r = subprocess.run(
                ["launchctl", "list", f"com.legacyai.tcc.{agent_id}"],
                capture_output=True, text=True, timeout=5,
            )
            assert r.returncode == 0, f"launchctl list failed: {r.stderr}"
            assert "Label" in r.stdout

            # 3. /api/automation/jobs surfaces it
            jobs = c.get("/api/automation/jobs").json()
            timer_jobs = [j for j in jobs if j.get("agent_id") == agent_id and j.get("type") == "launchd_timer"]
            assert timer_jobs, f"timer not in automation jobs: {jobs}"
            assert timer_jobs[0]["schedule"] == "86400s"
            assert timer_jobs[0]["status"] == "active"

            # 4. /api/agents/{id}/launchd returns the same info
            launchd_info = c.get(f"/api/agents/{agent_id}/launchd").json()
            assert launchd_info["loaded"] is True

        finally:
            # Cleanup: delete the agent — server unloads + removes the plist
            _delete_agent(c, agent_id)
            # Verify cleanup
            time.sleep(0.3)
            assert not os.path.exists(plist), f"plist not removed after delete: {plist}"


def test_launchd_disable_then_enable():
    """User can pause a scheduled agent without losing the schedule."""
    with _client() as c:
        agent = _create_agent(
            c,
            launchd_type="timer",
            launchd_interval=86400,
        )
        agent_id = agent["id"]
        try:
            # Disable
            r = c.post(f"/api/automation/{agent_id}/disable")
            assert r.status_code == 200, r.text
            assert r.json().get("disabled") is True

            # launchctl no longer knows about it
            r = subprocess.run(
                ["launchctl", "list", f"com.legacyai.tcc.{agent_id}"],
                capture_output=True, text=True, timeout=5,
            )
            assert r.returncode != 0  # not loaded

            # Re-enable
            r = c.post(f"/api/automation/{agent_id}/enable")
            assert r.status_code == 200, r.text
            assert r.json().get("enabled") is True

            r = subprocess.run(
                ["launchctl", "list", f"com.legacyai.tcc.{agent_id}"],
                capture_output=True, text=True, timeout=5,
            )
            assert r.returncode == 0
        finally:
            _delete_agent(c, agent_id)


def test_launchd_keepalive_plist_has_run_at_load():
    """keepalive agents must have RunAtLoad=true so they start immediately."""
    with _client() as c:
        agent = _create_agent(c, launchd_type="keepalive", command="bash -lc 'echo keepalive_$$ && sleep 5'")
        agent_id = agent["id"]
        try:
            content = open(_plist_path(agent_id)).read()
            assert "<key>KeepAlive</key>" in content
            assert "<key>RunAtLoad</key>" in content
        finally:
            _delete_agent(c, agent_id)


# ---- CRON integration ------------------------------------------------------


def test_cron_expression_surfaces_in_automation_jobs():
    """Cron-scheduled agents must appear in the automation jobs list with a computed next_run."""
    with _client() as c:
        agent = _create_agent(c, cron_expression="0 0 * * *")  # midnight daily
        agent_id = agent["id"]
        try:
            jobs = c.get("/api/automation/jobs").json()
            cron_jobs = [j for j in jobs if j.get("agent_id") == agent_id and j.get("type") == "cron"]
            assert cron_jobs, f"cron job missing from automation list: {jobs}"
            j = cron_jobs[0]
            assert j["schedule"] == "0 0 * * *"
            assert j["status"] == "scheduled"
            assert j["next_run"] is not None
            # next_run is in the future
            assert j["next_run"] > time.time()
        finally:
            _delete_agent(c, agent_id)


def test_cron_helper_returns_known_presets():
    """The cron helper drives the UI's preset dropdown — must always include the staples."""
    with _client() as c:
        presets = c.get("/api/cron/helper").json()
    labels = {p["label"]: p["expression"] for p in presets}
    assert labels.get("Every minute") == "* * * * *"
    assert labels.get("Every 5 minutes") == "*/5 * * * *"
    assert labels.get("Every hour") == "0 * * * *"
    assert labels.get("Daily at midnight") == "0 0 * * *"


@pytest.mark.skipif(
    os.environ.get("SKIP_SLOW_CRON", "0") == "1",
    reason="slow CRON fire test — set SKIP_SLOW_CRON=1 to skip in fast-test mode",
)
def test_cron_scheduler_actually_fires_due_agents():
    """Real-time test: every-minute cron expression must spawn the agent within 75s.

    The TCC cron scheduler runs as a background asyncio task that wakes every 60s.
    Worst case: agent created right after a tick → scheduler waits 60s → fires →
    we observe `running` status on the next /api/agents call. 75s leaves headroom.
    """
    sentinel_file = f"/tmp/tcc-cron-sentinel-{uuid.uuid4().hex[:8]}.txt"
    # Drop a sentinel file so we have proof the cron-fired process actually ran.
    cmd = f"bash -lc 'date >> {sentinel_file}; sleep 2'"

    with _client() as c:
        agent = _create_agent(
            c,
            cron_expression="* * * * *",  # every minute
            command=cmd,
        )
        agent_id = agent["id"]
        try:
            # Don't pre-spawn; the scheduler must fire it on its own.
            # Wait up to 75s for the scheduler to fire it
            fired = False
            deadline = time.time() + 75
            while time.time() < deadline:
                # 1. Check sentinel file (definitive evidence)
                if os.path.exists(sentinel_file) and os.path.getsize(sentinel_file) > 0:
                    fired = True
                    break
                # 2. Or check that /api/agents shows it as running / having run once
                status = c.get(f"/api/agents/{agent_id}/status").json()
                if status.get("status") in ("running", "exited") and status.get("uptime", 0) > 0:
                    # uptime > 0 means at least one process spawn happened
                    fired = True
                    break
                time.sleep(2)

            assert fired, (
                f"cron-scheduled agent did not fire within 75s; "
                f"sentinel={os.path.exists(sentinel_file)}; "
                f"status={c.get(f'/api/agents/{agent_id}/status').json()}"
            )
        finally:
            _delete_agent(c, agent_id)
            try:
                os.remove(sentinel_file)
            except FileNotFoundError:
                pass

