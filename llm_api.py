"""LLM-First API for Floyd's Unified Command Kernel.

Single endpoint, progressive disclosure design.
Small models use required fields only. Capable models add optional fields.
"""

import asyncio
import os
import time
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from output_analyzer import strip_ansi, detect_state, generate_hint, generate_tip

llm_router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class DoRequest(BaseModel):
    """The single entry point for all LLM interactions."""

    # Required fields (what small models use)
    action: str = Field(
        description="What to do. One of: list, read, run, stop, start, cancel, answer"
    )
    agent: Optional[str] = Field(
        default=None,
        description="Which terminal to act on (use the agent name)",
    )
    input: Optional[str] = Field(
        default=None,
        description="Command or text to send to the terminal",
    )

    # Optional fields (progressive disclosure for capable models)
    wait_until: Optional[str] = Field(
        default=None,
        description="Text to watch for before returning (plain text match, not regex)",
    )
    timeout: Optional[int] = Field(
        default=None,
        ge=1,
        le=300,
        description="Max seconds to wait (default: 5 for commands, 30 with wait_until)",
    )
    lines: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="Number of output lines to return (default: 50)",
    )
    include_advanced: Optional[bool] = Field(
        default=False,
        description="Include extra metadata in response (for capable models)",
    )


class AgentSummary(BaseModel):
    name: str
    status: str
    hint: str
    last_output: str


class AdvancedBlock(BaseModel):
    elapsed_ms: int = 0
    scrollback_bytes: int = 0
    output_lines_total: int = 0
    process_pid: Optional[int] = None
    uptime_seconds: float = 0.0
    matched_text: Optional[str] = None


class DoResponse(BaseModel):
    ok: bool
    output: Optional[str] = None
    status: Optional[str] = None
    hint: str
    actions_available: List[str]
    agents: Optional[List[AgentSummary]] = None
    error_message: Optional[str] = None
    tip: Optional[str] = None
    advanced: Optional[AdvancedBlock] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# These get populated at import time by server.py's module scope
# We import them lazily to avoid circular imports


def _get_server_state():
    """Lazy import of server module state."""
    import server

    return server


def _resolve_agent(name: str) -> tuple[str, dict]:
    """Find an agent by name (case-insensitive). Returns (agent_id, agent_dict).

    Raises HTTPException with helpful error if not found.
    """
    srv = _get_server_state()
    agents = srv.load_agents()

    # Exact match first
    for aid, agent in agents.items():
        if agent.get("name", "").lower() == name.lower():
            return aid, agent

    # Partial match
    matches = []
    for aid, agent in agents.items():
        if name.lower() in agent.get("name", "").lower():
            matches.append(agent.get("name", ""))

    available = sorted(a.get("name", "") for a in agents.values())
    if matches:
        suggestion = f"Did you mean: {', '.join(matches)}?"
    else:
        suggestion = (
            f"Available agents: {', '.join(available)}"
            if available
            else "No agents configured."
        )

    raise HTTPException(
        status_code=404,
        detail={
            "ok": False,
            "error_message": f"Agent '{name}' not found. {suggestion}",
            "hint": "Use action 'list' to see all agents.",
            "actions_available": ["list"],
        },
    )


def _get_clean_output(handle, max_lines: int = 50) -> tuple[str, list[str], bool]:
    """Get clean text output from a process handle.

    Returns (full_text, lines_list, was_truncated).
    """
    if handle is None:
        return "", [], False

    raw = handle.get_raw_scrollback().decode("utf-8", errors="replace")
    clean = strip_ansi(raw)
    all_lines = clean.split("\n")

    # Remove empty trailing line
    if all_lines and all_lines[-1] == "":
        all_lines = all_lines[:-1]

    truncated = len(all_lines) > max_lines
    output_lines = all_lines[-max_lines:]
    return "\n".join(output_lines), output_lines, truncated


def _get_state_for_handle(handle, agent_name: str) -> tuple[str, str]:
    """Get state and last line for a handle. Returns (state, last_line)."""
    if handle is None:
        return "stopped", ""

    raw = handle.get_raw_scrollback().decode("utf-8", errors="replace")
    clean = strip_ansi(raw)
    lines = clean.rstrip("\n").split("\n")
    last_line = lines[-1] if lines else ""

    state = detect_state(
        clean,
        process_alive=handle.alive,
        last_output_time=getattr(handle, "last_output_at", time.time()),
    )
    return state, last_line


# ---------------------------------------------------------------------------
# The single endpoint
# ---------------------------------------------------------------------------


@llm_router.post("/do", response_model=DoResponse)
async def do_action(req: DoRequest) -> DoResponse:
    """Single entry point for all LLM terminal interactions.

    Actions: list, read, run, stop, start, cancel, answer
    """
    action = req.action.lower().strip()

    if action == "list":
        return await _action_list(req)
    elif action == "read":
        return await _action_read(req)
    elif action == "run":
        return await _action_run(req)
    elif action == "stop":
        return await _action_stop(req)
    elif action == "start":
        return await _action_start(req)
    elif action == "cancel":
        return await _action_cancel(req)
    elif action == "answer":
        return await _action_answer(req)
    else:
        return DoResponse(
            ok=False,
            hint=f"Unknown action '{action}'. Use one of the available actions.",
            error_message=f"Action '{action}' is not recognized. Valid actions: list, read, run, stop, start, cancel, answer",
            actions_available=[
                "list",
                "read",
                "run",
                "stop",
                "start",
                "cancel",
                "answer",
            ],
        )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _action_list(req: DoRequest) -> DoResponse:
    """Show all terminals and their current state."""
    srv = _get_server_state()
    agents = srv.load_agents()

    if not agents:
        return DoResponse(
            ok=True,
            output="No agents configured.",
            status="ready",
            hint="No terminals are set up yet. Create agents through the web UI or API.",
            actions_available=["list"],
        )

    summaries = []
    for aid, agent in agents.items():
        handle = srv.running_processes.get(aid)
        state, last_line = _get_state_for_handle(handle, agent.get("name", ""))

        # Truncate last_output for summary
        last_output = last_line.strip()[-80:] if last_line.strip() else "(no output)"

        summaries.append(
            AgentSummary(
                name=agent.get("name", aid),
                status=state,
                hint=generate_hint(state, agent.get("name", "")),
                last_output=last_output,
            )
        )

    # Build readable text output
    lines = []
    for s in summaries:
        lines.append(f"  {s.name} [{s.status}] — {s.last_output}")

    output_text = "\n".join(lines)
    agent_names = [s.name for s in summaries]

    return DoResponse(
        ok=True,
        output=output_text,
        status="ready",
        hint=f"There are {len(summaries)} terminals. Use action 'read' with an agent name to see full output. Use 'run' to send a command.",
        actions_available=["list", "read", "run", "stop", "start", "cancel", "answer"],
        agents=summaries,
    )


async def _action_read(req: DoRequest) -> DoResponse:
    """Get recent output from a terminal."""
    if not req.agent:
        return DoResponse(
            ok=False,
            error_message="'agent' is required for action 'read'. Specify which terminal to read.",
            hint="Use action 'list' to see available agents, then 'read' with the agent name.",
            actions_available=["list"],
        )

    srv = _get_server_state()
    agent_id, agent = _resolve_agent(req.agent)
    handle = srv.running_processes.get(agent_id)

    max_lines = req.lines or 50
    output_text, output_lines, truncated = _get_clean_output(handle, max_lines)
    state, last_line = _get_state_for_handle(handle, agent.get("name", ""))

    hint = generate_hint(state, agent.get("name", ""), last_line)
    tip = generate_tip(state, "read", truncated=truncated)

    response = DoResponse(
        ok=True,
        output=output_text if output_text else "(no output yet)",
        status=state,
        hint=hint,
        actions_available=["list", "read", "run", "stop", "start", "cancel", "answer"],
        tip=tip,
    )

    if req.include_advanced and handle:
        response.advanced = AdvancedBlock(
            scrollback_bytes=len(handle.scrollback),
            output_lines_total=len(
                handle.get_raw_scrollback()
                .decode("utf-8", errors="replace")
                .split("\n")
            ),
            process_pid=handle.process.pid if handle.alive else None,
            uptime_seconds=time.time() - handle.started_at,
        )

    return response


async def _action_run(req: DoRequest) -> DoResponse:
    """Send a command and wait for the result."""
    if not req.agent:
        return DoResponse(
            ok=False,
            error_message="'agent' is required for action 'run'. Specify which terminal.",
            hint="Use action 'list' to see available agents.",
            actions_available=["list"],
        )
    if not req.input:
        return DoResponse(
            ok=False,
            error_message="'input' is required for action 'run'. Provide the command to execute.",
            hint="Set 'input' to the command you want to run.",
            actions_available=["list", "read", "run"],
        )

    srv = _get_server_state()
    agent_id, agent = _resolve_agent(req.agent)
    handle = srv.running_processes.get(agent_id)

    # Auto-start if not running
    if handle is None or not handle.alive:
        try:
            handle = await srv.spawn_process(agent_id, agent)
            # Wait briefly for shell prompt
            await asyncio.sleep(0.5)
        except Exception as e:
            return DoResponse(
                ok=False,
                error_message=f"Failed to start '{req.agent}': {str(e)}",
                hint="The terminal could not be started. Check the agent configuration.",
                actions_available=["list"],
            )

    # Record position before sending input
    before_len = len(handle.scrollback)

    # Send input (auto-append newline if missing)
    command = req.input
    if not command.endswith("\n"):
        command += "\n"

    try:
        os.write(handle.master_fd, command.encode("utf-8"))
    except OSError as e:
        return DoResponse(
            ok=False,
            error_message=f"Failed to send input: {str(e)}",
            hint="The terminal may have crashed. Try action 'start' to restart it.",
            status="stopped",
            actions_available=["start", "list"],
        )

    # Wait for output to stabilize
    timeout_seconds = req.timeout or (30 if req.wait_until else 5)
    wait_until_text = req.wait_until
    start_time = time.time()
    last_change_time = start_time
    last_seen_len = before_len
    matched_text = None

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout_seconds:
            break

        await asyncio.sleep(0.1)

        current_len = len(handle.scrollback)
        if current_len != last_seen_len:
            # New output since last check
            last_seen_len = current_len
            last_change_time = time.time()

            new_bytes = bytes(handle.scrollback[before_len:])
            new_text = strip_ansi(new_bytes.decode("utf-8", errors="replace"))

            # Check wait_until condition
            if wait_until_text and wait_until_text.lower() in new_text.lower():
                matched_text = wait_until_text
                break

        # If no wait_until, settle when output is stable for 0.5s
        if not wait_until_text:
            idle_time = time.time() - last_change_time
            if idle_time > 0.5 and last_seen_len > before_len:
                break

    # Capture the new output
    elapsed_ms = int((time.time() - start_time) * 1000)
    new_output_bytes = bytes(handle.scrollback[before_len:])
    new_output = strip_ansi(new_output_bytes.decode("utf-8", errors="replace"))

    # Clean up: remove the echoed command from output
    output_lines = new_output.split("\n")
    if output_lines and req.input.strip() in output_lines[0]:
        output_lines = output_lines[1:]
    new_output = "\n".join(output_lines).strip()

    # Detect final state
    state, last_line = _get_state_for_handle(handle, agent.get("name", ""))
    hint = generate_hint(state, agent.get("name", ""), last_line)
    tip = generate_tip(state, "run", elapsed_ms=elapsed_ms)

    # Truncate if very long
    max_lines = req.lines or 50
    all_lines = new_output.split("\n")
    truncated = len(all_lines) > max_lines
    if truncated:
        new_output = "\n".join(all_lines[-max_lines:])
        if not tip:
            tip = "Output was truncated. Add 'lines': 200 to get more."

    response = DoResponse(
        ok=True,
        output=new_output if new_output else "(no output)",
        status=state,
        hint=hint,
        actions_available=["list", "read", "run", "stop", "cancel"],
        tip=tip,
    )

    if req.include_advanced:
        response.advanced = AdvancedBlock(
            elapsed_ms=elapsed_ms,
            scrollback_bytes=len(handle.scrollback),
            output_lines_total=len(all_lines),
            process_pid=handle.process.pid if handle.alive else None,
            uptime_seconds=time.time() - handle.started_at,
            matched_text=matched_text,
        )

    return response


async def _action_stop(req: DoRequest) -> DoResponse:
    """Stop a running terminal process."""
    if not req.agent:
        return DoResponse(
            ok=False,
            error_message="'agent' is required for action 'stop'.",
            hint="Use action 'list' to see which agents are running.",
            actions_available=["list"],
        )

    srv = _get_server_state()
    agent_id, agent = _resolve_agent(req.agent)
    handle = srv.running_processes.get(agent_id)

    if handle is None or not handle.alive:
        return DoResponse(
            ok=True,
            output="Already stopped.",
            status="stopped",
            hint=f"'{req.agent}' was already stopped. Use action 'start' to launch it.",
            actions_available=["start", "list"],
        )

    await srv.kill_process(agent_id)

    return DoResponse(
        ok=True,
        output="Process stopped.",
        status="stopped",
        hint=f"'{req.agent}' has been stopped. Use action 'start' to restart it.",
        actions_available=["start", "list"],
    )


async def _action_start(req: DoRequest) -> DoResponse:
    """Start a stopped terminal process."""
    if not req.agent:
        return DoResponse(
            ok=False,
            error_message="'agent' is required for action 'start'.",
            hint="Use action 'list' to see available agents.",
            actions_available=["list"],
        )

    srv = _get_server_state()
    agent_id, agent = _resolve_agent(req.agent)
    handle = srv.running_processes.get(agent_id)

    if handle and handle.alive:
        state, last_line = _get_state_for_handle(handle, agent.get("name", ""))
        return DoResponse(
            ok=True,
            output="Already running.",
            status=state,
            hint=f"'{req.agent}' is already running. Use 'run' to send it a command.",
            actions_available=["run", "read", "stop", "cancel", "list"],
        )

    try:
        handle = await srv.spawn_process(agent_id, agent)
        # Wait for initial output
        await asyncio.sleep(1.0)

        max_lines = req.lines or 20
        output_text, _, truncated = _get_clean_output(handle, max_lines)
        state, last_line = _get_state_for_handle(handle, agent.get("name", ""))

        return DoResponse(
            ok=True,
            output=output_text if output_text else "(starting...)",
            status=state,
            hint=f"'{req.agent}' has been started. Use 'run' to send commands.",
            actions_available=["run", "read", "stop", "cancel", "list"],
        )
    except Exception as e:
        return DoResponse(
            ok=False,
            error_message=f"Failed to start '{req.agent}': {str(e)}",
            hint="Check that the agent's directory and command are valid.",
            actions_available=["list"],
        )


async def _action_cancel(req: DoRequest) -> DoResponse:
    """Send Ctrl+C to interrupt a running command."""
    if not req.agent:
        return DoResponse(
            ok=False,
            error_message="'agent' is required for action 'cancel'.",
            hint="Use action 'list' to see running agents.",
            actions_available=["list"],
        )

    srv = _get_server_state()
    agent_id, agent = _resolve_agent(req.agent)
    handle = srv.running_processes.get(agent_id)

    if handle is None or not handle.alive:
        return DoResponse(
            ok=True,
            output="Not running — nothing to cancel.",
            status="stopped",
            hint=f"'{req.agent}' is not running. Use 'start' to launch it.",
            actions_available=["start", "list"],
        )

    # Send Ctrl+C (ETX byte)
    try:
        os.write(handle.master_fd, b"\x03")
    except OSError as e:
        return DoResponse(
            ok=False,
            error_message=f"Failed to send cancel: {str(e)}",
            hint="The terminal may have crashed.",
            actions_available=["start", "list"],
        )

    # Wait briefly for the interrupt to take effect
    await asyncio.sleep(0.3)

    state, last_line = _get_state_for_handle(handle, agent.get("name", ""))

    return DoResponse(
        ok=True,
        output="Interrupt sent (Ctrl+C).",
        status=state,
        hint=f"Sent cancel to '{req.agent}'. "
        + generate_hint(state, agent.get("name", ""), last_line),
        actions_available=["run", "read", "stop", "list"],
    )


async def _action_answer(req: DoRequest) -> DoResponse:
    """Reply to a terminal prompt (password, y/n, etc.) without appending newline by default."""
    if not req.agent:
        return DoResponse(
            ok=False,
            error_message="'agent' is required for action 'answer'.",
            hint="Use action 'list' to see which agents need answers.",
            actions_available=["list"],
        )
    if req.input is None:
        return DoResponse(
            ok=False,
            error_message="'input' is required for action 'answer'. Provide your response to the prompt.",
            hint="Set 'input' to your answer (e.g., 'y', 'n', or a password).",
            actions_available=["read", "list"],
        )

    srv = _get_server_state()
    agent_id, agent = _resolve_agent(req.agent)
    handle = srv.running_processes.get(agent_id)

    if handle is None or not handle.alive:
        return DoResponse(
            ok=False,
            error_message=f"'{req.agent}' is not running.",
            hint="Use action 'start' to launch it first.",
            status="stopped",
            actions_available=["start", "list"],
        )

    # Record position
    before_len = len(handle.scrollback)

    # Send the answer with newline
    answer = req.input
    if not answer.endswith("\n"):
        answer += "\n"

    try:
        os.write(handle.master_fd, answer.encode("utf-8"))
    except OSError as e:
        return DoResponse(
            ok=False,
            error_message=f"Failed to send answer: {str(e)}",
            hint="The terminal may have crashed.",
            actions_available=["start", "list"],
        )

    # Wait for response
    timeout_seconds = req.timeout or 5
    start_time = time.time()
    last_change_time = start_time

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout_seconds:
            break

        await asyncio.sleep(0.1)

        current_len = len(handle.scrollback)
        if current_len != before_len:
            last_change_time = time.time()

        # Settle after 0.5s of no new output
        idle_time = time.time() - last_change_time
        if idle_time > 0.5 and current_len > before_len:
            break

    # Get new output
    elapsed_ms = int((time.time() - start_time) * 1000)
    new_output_bytes = bytes(handle.scrollback[before_len:])
    new_output = strip_ansi(new_output_bytes.decode("utf-8", errors="replace")).strip()

    state, last_line = _get_state_for_handle(handle, agent.get("name", ""))
    hint = generate_hint(state, agent.get("name", ""), last_line)

    response = DoResponse(
        ok=True,
        output=new_output if new_output else "(no response yet)",
        status=state,
        hint=hint,
        actions_available=["run", "read", "stop", "cancel", "list"],
    )

    if req.include_advanced:
        response.advanced = AdvancedBlock(
            elapsed_ms=elapsed_ms,
            scrollback_bytes=len(handle.scrollback),
            process_pid=handle.process.pid if handle.alive else None,
            uptime_seconds=time.time() - handle.started_at,
        )

    return response

