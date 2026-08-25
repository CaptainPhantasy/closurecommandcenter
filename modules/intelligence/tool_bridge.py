"""
Tool Bridge — maps GLM tool_calls to ATerm MCP actions via /api/do.

When the LLM emits a tool_call (function_call), the Tool Bridge:
  1. Validates via Hook Engine (pre hooks)
  2. Translates the call to an ATerm action
  3. Executes via ATerm's /api/do HTTP endpoint
  4. Distills the result back into LLM context
  5. Validates via Hook Engine (post hooks)

Progressive disclosure:
  Tier 1 (3-9B models):   5 core actions  (run, read, list, create, stop)
  Tier 2 (10-70B models): 14 actions      (full set minus automate/batch)
  Tier 3 (70B+ models):   19 actions      (everything including automate/batch)

ATerm action map:
  aterm_run       → { action: "run", cmd: "..." }
  aterm_read      → { action: "read", id: "..." }
  aterm_list      → { action: "list" }
  aterm_create    → { action: "create", cmd: "..." }
  aterm_stop      → { action: "stop", id: "..." }
  aterm_cancel    → { action: "cancel", id: "..." }
  aterm_answer    → { action: "answer", id: "...", text: "..." }
  aterm_delete    → { action: "delete", id: "..." }
  aterm_note      → { action: "note", id: "...", text: "..." }
  aterm_search    → { action: "search", query: "..." }
  aterm_start     → { action: "start", id: "..." }
  aterm_broadcast → { action: "broadcast", text: "..." }
  aterm_history   → { action: "history" }
  aterm_checkpoint→ { action: "checkpoint", label: "..." }
  aterm_record    → { action: "record", id: "..." }
  aterm_verify    → { action: "verify", id: "..." }
  aterm_batch     → { action: "batch", actions: [...] }
  aterm_automate  → { action: "automate", ... }
  aterm_bridge    → { action: "bridge", ... }
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .models import get_model, ModelTier, ModelCapability
from .hook_engine import HookEngine, HookContext, HookPhase

# ATerm HTTP API
ATERM_BASE = "http://localhost:9600"


# ---------------------------------------------------------------------------
# Tool definitions for GLM function calling
# ---------------------------------------------------------------------------

# Tier 1: Minimal set for small models
TIER1_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal_run",
            "description": "Run a shell command in a terminal. Returns the terminal ID and initial output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "name": {"type": "string", "description": "Optional name for the terminal"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_read",
            "description": "Read output from a running terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "lines": {"type": "integer", "description": "Max lines to read (default 100)"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_list",
            "description": "List all terminals and their status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_create",
            "description": "Create a new terminal session with an initial command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Initial command"},
                    "name": {"type": "string", "description": "Terminal name"},
                    "cwd": {"type": "string", "description": "Working directory"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_stop",
            "description": "Stop a running terminal.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Terminal ID to stop"}},
                "required": ["id"],
            },
        },
    },
]

# Tier 2 additions (10-70B models)
TIER2_EXTRA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal_answer",
            "description": "Send input to a terminal waiting for user input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "text": {"type": "string", "description": "Text to send"},
                },
                "required": ["id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_search",
            "description": "Search terminal outputs for a pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search pattern"},
                    "id": {"type": "string", "description": "Optional terminal ID to restrict search"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_note",
            "description": "Add a note/annotation to a terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "text": {"type": "string", "description": "Note content"},
                },
                "required": ["id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_broadcast",
            "description": "Send a message to all running terminals.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Message to broadcast"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_history",
            "description": "Get command history across terminals.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_cancel",
            "description": "Cancel the current command in a terminal.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Terminal ID"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_checkpoint",
            "description": "Create a checkpoint of terminal state for later restoration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Checkpoint label"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_delete",
            "description": "Delete a terminal session and its history.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Terminal ID"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_start",
            "description": "Restart a stopped terminal.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Terminal ID"}},
                "required": ["id"],
            },
        },
    },
]

# Tier 3 additions (70B+)
TIER3_EXTRA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal_record",
            "description": "Start/stop recording terminal output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "action": {"type": "string", "enum": ["start", "stop"]},
                },
                "required": ["id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_verify",
            "description": "Verify terminal output against expected pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "expected": {"type": "string", "description": "Expected output pattern"},
                },
                "required": ["id", "expected"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_batch",
            "description": "Execute multiple actions in sequence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of actions to execute",
                    },
                },
                "required": ["actions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_automate",
            "description": "Set up automated monitoring/reaction for a terminal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "trigger": {"type": "string", "description": "Pattern to watch for"},
                    "response": {"type": "string", "description": "Command to run when triggered"},
                },
                "required": ["id", "trigger", "response"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_bridge",
            "description": "Bridge terminal output to another service/endpoint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Terminal ID"},
                    "url": {"type": "string", "description": "Target URL"},
                },
                "required": ["id", "url"],
            },
        },
    },
]


def get_tools_for_model(model_id: str) -> list[dict]:
    """Return the tool set appropriate for the model's tier."""
    model = get_model(model_id)
    if not model:
        return TIER1_TOOLS  # Default to safest tier

    if model.tier == ModelTier.FLAGSHIP:
        return TIER1_TOOLS + TIER2_EXTRA_TOOLS + TIER3_EXTRA_TOOLS
    elif model.tier in (ModelTier.STRONG,):
        return TIER1_TOOLS + TIER2_EXTRA_TOOLS
    else:
        return TIER1_TOOLS


# ---------------------------------------------------------------------------
# GLM tool_name → ATerm action mapping
# ---------------------------------------------------------------------------

TOOL_ACTION_MAP: dict[str, str] = {
    "terminal_run": "run",
    "terminal_read": "read",
    "terminal_list": "list",
    "terminal_create": "create",
    "terminal_stop": "stop",
    "terminal_cancel": "cancel",
    "terminal_answer": "answer",
    "terminal_delete": "delete",
    "terminal_note": "note",
    "terminal_search": "search",
    "terminal_start": "start",
    "terminal_broadcast": "broadcast",
    "terminal_history": "history",
    "terminal_checkpoint": "checkpoint",
    "terminal_record": "record",
    "terminal_verify": "verify",
    "terminal_batch": "batch",
    "terminal_automate": "automate",
    "terminal_bridge": "bridge",
}

# Parameter name mapping: GLM param → ATerm param
PARAM_MAP: dict[str, str] = {
    "command": "cmd",
    "text": "text",
    "query": "query",
    "lines": "lines",
    "label": "label",
    "expected": "expected",
    "actions": "actions",
    "trigger": "trigger",
    "response": "response",
    "url": "url",
    "action": "action",
    "cwd": "cwd",
    "name": "name",
}


@dataclass
class BridgeResult:
    success: bool
    action: str
    tool_name: str
    result: str
    distillation: str = ""  # Condensed version for LLM context
    timing_ms: float = 0.0
    error: str = ""


class ToolBridge:
    """Translates GLM tool_calls to ATerm actions and back."""

    def __init__(self, hook_engine: HookEngine, aterm_base: str = ATERM_BASE) -> None:
        self._hooks = hook_engine
        self._aterm_base = aterm_base

    async def execute(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        model: str = "",
    ) -> BridgeResult:
        """Execute a tool call through the full pipeline."""
        start = time.time()

        # 1. Map tool name to ATerm action
        action = TOOL_ACTION_MAP.get(tool_name)
        if not action:
            return BridgeResult(
                success=False,
                action="unknown",
                tool_name=tool_name,
                result="",
                error=f"Unknown tool: {tool_name}",
                timing_ms=(time.time() - start) * 1000,
            )

        # 2. Translate arguments
        aterm_args = self._translate_args(action, arguments)

        # 3. Run PRE hooks
        ctx = HookContext(
            session_id=session_id,
            tool_name=tool_name,
            arguments=aterm_args,
            model=model,
        )
        hook_outcome = await self._hooks.run_pre(ctx)
        if hook_outcome.decision.value == "block":
            return BridgeResult(
                success=False,
                action=action,
                tool_name=tool_name,
                result="",
                error=f"Blocked by hook: {hook_outcome.reason}",
                timing_ms=(time.time() - start) * 1000,
            )
        if hook_outcome.modified_args:
            aterm_args.update(hook_outcome.modified_args)

        # 4. Execute via ATerm /api/do
        try:
            result_text = await self._call_aterm(action, aterm_args)
        except Exception as e:
            return BridgeResult(
                success=False,
                action=action,
                tool_name=tool_name,
                result="",
                error=f"ATerm error: {e}",
                timing_ms=(time.time() - start) * 1000,
            )

        # 5. Run POST hooks
        ctx.result = result_text
        await self._hooks.run_post(ctx)

        # 6. Distill result for LLM context
        distillation = self._distill(result_text, tool_name)

        return BridgeResult(
            success=True,
            action=action,
            tool_name=tool_name,
            result=result_text,
            distillation=distillation,
            timing_ms=(time.time() - start) * 1000,
        )

    def _translate_args(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """Map GLM function args to ATerm /api/do args."""
        aterm_args: dict[str, Any] = {"action": action}
        for key, val in args.items():
            mapped_key = PARAM_MAP.get(key, key)
            aterm_args[mapped_key] = val
        return aterm_args

    async def _call_aterm(self, action: str, args: dict[str, Any]) -> str:
        """HTTP call to ATerm's /api/do endpoint."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._aterm_base}/api/do",
                json=args,
            )
            resp.raise_for_status()
            return resp.text

    def _distill(self, result: str, tool_name: str) -> str:
        """Condense ATerm output for LLM context window efficiency."""
        if not result:
            return "(empty output)"

        # For terminal_run/read, truncate large outputs
        if tool_name in ("terminal_run", "terminal_read"):
            lines = result.strip().split("\n")
            if len(lines) > 50:
                head = "\n".join(lines[:25])
                tail = "\n".join(lines[-25:])
                return f"{head}\n... ({len(lines) - 50} lines truncated) ...\n{tail}"
            return result

        # For terminal_list, keep as-is (usually compact)
        return result

    def get_tool_definitions(self, model: str = "") -> list[dict]:
        """Get tool definitions appropriate for the model."""
        return get_tools_for_model(model)

