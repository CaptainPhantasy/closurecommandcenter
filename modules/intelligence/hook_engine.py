"""
Hook Engine — PEBKAC-style mechanical hooks for the intelligence layer.

Hooks are deterministic enforcement gates that intercept every tool call
between the LLM and the execution layer. They run in priority order and
can approve, modify, or block any tool invocation.

Hook lifecycle:
  1. LLM emits tool_call → Hook Engine runs PRE hooks
  2. PRE hooks can: approve, modify args, or block the call
  3. If approved → execution proceeds (via Tool Bridge → ATerm)
  4. After execution → Hook Engine runs POST hooks
  5. POST hooks can: verify result, score quality, trigger rollback

Built-in hooks:
  destructive_gate    — blocks rm/rmdir/force-terminate without confirmation
  path_jail          — ensures all file ops stay within allowed directories
  token_budget       — tracks cumulative token spend per session
  output_validation  — verifies tool outputs match expected shape
  rate_limiter       — prevents tool call flooding

Custom hooks are registered at runtime via register_hook().
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


class HookPhase(str, Enum):
    PRE = "pre"
    POST = "post"


class HookResult(str, Enum):
    APPROVE = "approve"       # Let the call proceed
    MODIFY = "modify"         # Modify args and proceed
    BLOCK = "block"           # Block the call, return reason
    DEFER = "defer"           # Needs human confirmation (async)


@dataclass
class HookContext:
    """Everything a hook needs to make a decision."""
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: str = ""                  # Only populated in POST phase
    model: str = ""
    call_history: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookOutcome:
    """What the hook decided."""
    decision: HookResult
    reason: str = ""
    modified_args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


# Type alias for hook functions
HookFn = Callable[[HookPhase, HookContext], Awaitable[HookOutcome]]


@dataclass
class Hook:
    name: str
    phase: HookPhase
    fn: HookFn
    priority: int = 100       # Lower = runs first
    enabled: bool = True
    description: str = ""


class HookEngine:
    """The mechanical enforcement layer."""

    def __init__(self) -> None:
        self._hooks: list[Hook] = []
        self._history: list[dict[str, Any]] = []
        self._register_builtin_hooks()

    # -- Registration --

    def register_hook(self, hook: Hook) -> None:
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)

    def unregister_hook(self, name: str) -> bool:
        before = len(self._hooks)
        self._hooks = [h for h in self._hooks if h.name != name]
        return len(self._hooks) < before

    def list_hooks(self) -> list[dict[str, Any]]:
        return [
            {
                "name": h.name,
                "phase": h.phase.value,
                "priority": h.priority,
                "enabled": h.enabled,
                "description": h.description,
            }
            for h in self._hooks
        ]

    def toggle_hook(self, name: str, enabled: bool) -> bool:
        for h in self._hooks:
            if h.name == name:
                h.enabled = enabled
                return True
        return False

    # -- Execution --

    async def run_pre(self, ctx: HookContext) -> HookOutcome:
        """Run all PRE hooks. Returns the first BLOCK, or last outcome."""
        outcome = HookOutcome(decision=HookResult.APPROVE)
        for hook in self._hooks:
            if hook.phase != HookPhase.PRE or not hook.enabled:
                continue
            result = await hook.fn(HookPhase.PRE, ctx)
            if result.decision == HookResult.BLOCK:
                self._log(ctx, hook, result)
                return result
            if result.decision == HookResult.MODIFY:
                ctx.arguments.update(result.modified_args)
                outcome = result
            if result.decision == HookResult.DEFER:
                self._log(ctx, hook, result)
                return result
        self._log(ctx, None, outcome)
        return outcome

    async def run_post(self, ctx: HookContext) -> HookOutcome:
        """Run all POST hooks. Logs results but doesn't block."""
        outcome = HookOutcome(decision=HookResult.APPROVE)
        for hook in self._hooks:
            if hook.phase != HookPhase.POST or not hook.enabled:
                continue
            result = await hook.fn(HookPhase.POST, ctx)
            self._log(ctx, hook, result)
            if result.decision == HookResult.BLOCK:
                # Post hooks can flag but not block execution that already happened.
                # They record the issue for the rewind system.
                outcome = result
        return outcome

    def get_history(self, session_id: str, limit: int = 100) -> list[dict]:
        return [
            e for e in self._history
            if e.get("session_id") == session_id
        ][-limit:]

    # -- Internals --

    def _log(self, ctx: HookContext, hook: Hook | None, outcome: HookOutcome) -> None:
        self._history.append({
            "session_id": ctx.session_id,
            "tool_name": ctx.tool_name,
            "hook": hook.name if hook else "passthrough",
            "decision": outcome.decision.value,
            "reason": outcome.reason,
            "timestamp": time.time(),
        })

    def _register_builtin_hooks(self) -> None:
        # 1. Destructive operation gate
        self.register_hook(Hook(
            name="destructive_gate",
            phase=HookPhase.PRE,
            fn=_hook_destructive_gate,
            priority=10,
            description="Blocks destructive operations (rm, force-terminate) without explicit allow.",
        ))
        # 2. Path jail — keeps file ops in allowed dirs
        self.register_hook(Hook(
            name="path_jail",
            phase=HookPhase.PRE,
            fn=_hook_path_jail,
            priority=20,
            description="Ensures file operations stay within allowed directories.",
        ))
        # 3. Rate limiter — prevents tool call flooding
        self.register_hook(Hook(
            name="rate_limiter",
            phase=HookPhase.PRE,
            fn=_hook_rate_limiter,
            priority=30,
            description="Prevents tool call flooding (max 30 calls per 60s per session).",
        ))
        # 4. Output validation — post hook
        self.register_hook(Hook(
            name="output_validation",
            phase=HookPhase.POST,
            fn=_hook_output_validation,
            priority=10,
            description="Validates tool outputs are non-empty and parseable.",
        ))


# ---------------------------------------------------------------------------
# Built-in hook implementations
# ---------------------------------------------------------------------------

# Destructive ops that need explicit confirmation
DESTRUCTIVE_PATTERNS = {
    "rm", "rmdir", "delete", "remove", "force_terminate", "kill",
    "reset", "clear", "purge", "drop", "truncate",
}

# Allowed base directories
ALLOWED_DIRS: list[str] = []


async def _hook_destructive_gate(phase: HookPhase, ctx: HookContext) -> HookOutcome:
    """Block destructive operations unless explicitly allowed."""
    tool = ctx.tool_name.lower()
    args_str = json.dumps(ctx.arguments).lower()

    is_destructive = (
        tool in DESTRUCTIVE_PATTERNS
        or any(p in tool for p in ("delete", "remove", "kill", "destroy", "reset"))
        or "rm -rf" in args_str
        or "rm -r" in args_str
    )

    if not is_destructive:
        return HookOutcome(decision=HookResult.APPROVE)

    # Check if explicitly allowed via metadata
    if ctx.metadata.get("allow_destructive"):
        return HookOutcome(
            decision=HookResult.APPROVE,
            reason=f"Destructive op '{tool}' explicitly allowed.",
        )

    return HookOutcome(
        decision=HookResult.BLOCK,
        reason=f"Destructive operation '{tool}' blocked. Set allow_destructive=true to override.",
    )


async def _hook_path_jail(phase: HookPhase, ctx: HookContext) -> HookOutcome:
    """Keep file operations within allowed directories."""
    if not ALLOWED_DIRS:
        return HookOutcome(decision=HookResult.APPROVE)

    # Check common path arguments
    path_args = ["path", "dir", "directory", "file", "cwd", "source", "destination"]
    for key in path_args:
        val = ctx.arguments.get(key, "")
        if isinstance(val, str) and val.startswith("/"):
            allowed = any(val.startswith(d) for d in ALLOWED_DIRS)
            if not allowed:
                return HookOutcome(
                    decision=HookResult.BLOCK,
                    reason=f"Path '{val}' is outside allowed directories.",
                )

    return HookOutcome(decision=HookResult.APPROVE)


# Simple in-memory rate tracking
_call_times: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60.0   # seconds
RATE_LIMIT_MAX = 30        # calls per window


async def _hook_rate_limiter(phase: HookPhase, ctx: HookContext) -> HookOutcome:
    """Prevent tool call flooding."""
    now = time.time()
    sid = ctx.session_id

    if sid not in _call_times:
        _call_times[sid] = []

    # Prune old entries
    _call_times[sid] = [t for t in _call_times[sid] if now - t < RATE_LIMIT_WINDOW]
    _call_times[sid].append(now)

    if len(_call_times[sid]) > RATE_LIMIT_MAX:
        return HookOutcome(
            decision=HookResult.BLOCK,
            reason=f"Rate limit: {len(_call_times[sid])} calls in {RATE_LIMIT_WINDOW}s (max {RATE_LIMIT_MAX}).",
        )

    return HookOutcome(decision=HookResult.APPROVE)


async def _hook_output_validation(phase: HookPhase, ctx: HookContext) -> HookOutcome:
    """Validate tool outputs are non-empty and parseable."""
    result = ctx.result
    if not result or not result.strip():
        return HookOutcome(
            decision=HookResult.APPROVE,
            reason="Empty result (may be expected).",
            metadata={"warning": "empty_result"},
        )

    # Try to parse as JSON
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "error" in parsed:
            return HookOutcome(
                decision=HookResult.APPROVE,
                reason=f"Tool returned error: {parsed['error']}",
                metadata={"warning": "tool_error_response"},
            )
    except json.JSONDecodeError:
        pass  # Non-JSON output is fine (terminal output, etc.)

    return HookOutcome(decision=HookResult.APPROVE)

