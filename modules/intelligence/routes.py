"""
Intelligence API routes — wires the intelligence layer into the Kernel's FastAPI app.

Mount these routes into server.py via:
  from modules.intelligence.routes import mount_intelligence_routes
  mount_intelligence_routes(app)

Endpoints:
  GET  /api/intel/models              — list all GLM models
  POST /api/intel/session              — create a new chat session
  GET  /api/intel/session/{id}         — get session details
  GET  /api/intel/session/{id}/messages — get session messages
  DELETE /api/intel/session/{id}        — delete session
  POST /api/intel/chat/stream          — stream a chat completion (SSE)
  GET  /api/intel/task/{id}/stream     — subscribe to task events (SSE, multi-tab)
  GET  /api/intel/tasks                — list active/recent tasks
  GET  /api/intel/task/{id}            — get task status
  POST /api/intel/task/{id}/cancel     — cancel a running task
  POST /api/intel/task/{id}/interact   — submit user interaction response
  GET  /api/intel/session/{id}/tools   — get tool call history
  GET  /api/intel/session/{id}/checkpoints — list checkpoints
  POST /api/intel/checkpoint           — create checkpoint
  POST /api/intel/restore/{cp_id}      — restore to checkpoint
  GET  /api/intel/diff/{a}/{b}         — diff two checkpoints
  GET  /api/intel/hooks                — list active hooks
  POST /api/intel/hooks/{name}/toggle  — enable/disable hook
  GET  /api/intel/stats                — usage statistics
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pathlib import Path as _PathLib
import json as _json

from .conversation import ConversationStore, Message
from .hook_engine import HookEngine
from .model_router import ModelRouter
from .tool_bridge import ToolBridge
from .rewind import RewindSystem
from .models import model_list_api
from .workflow import (
    workflow_service,
    SSEEvent,
    ActivityType,
    TaskStatus,
)

# Singleton instances (shared across all requests)
_store = ConversationStore()
_hooks = HookEngine()
_router = ModelRouter()
_rewind = RewindSystem()
_bridge = ToolBridge(_hooks)

# Vault resolution (mirrors server.py's _we_vault_read pattern)
_VAULT_PATH = _PathLib.home() / ".config" / "keys.json"

# Z.ai default provider
_ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_ZAI_VAULT_ID = "zai"  # vault key ID for Z.ai API key


def _resolve_credentials(provider: dict | None = None) -> tuple[str, str]:
    """Resolve API credentials: explicit provider > vault Z.ai key > error.

    Returns (api_key, base_url).
    """
    api_key = ""
    base_url = ""

    # 1. Explicit inline provider
    if provider:
        api_key = provider.get("apiKey", "")
        base_url = provider.get("baseUrl", "").rstrip("/")
        vault_id = provider.get("id", "")
        # If provider has a vault ID, look it up
        if vault_id and not api_key:
            vault = _read_vault()
            api_key = vault.get(vault_id, "")

    if api_key and base_url:
        return api_key, base_url

    # 2. Fall back to Z.ai default from vault
    vault = _read_vault()
    zai_key = vault.get(_ZAI_VAULT_ID, "")
    if zai_key:
        return zai_key, _ZAI_BASE_URL

    raise HTTPException(
        400,
        "No API key configured. Store Z.ai key via /api/vault/set with id='zai', "
        "or pass provider.apiKey in the request body.",
    )


def _read_vault() -> dict:
    """Read the Kernel's vault file (~/.config/keys.json)."""
    try:
        txt = _VAULT_PATH.read_text(encoding="utf-8")
        data = _json.loads(txt)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return {}


def _sse_format(event: dict[str, Any]) -> str:
    """Format an event dict as an SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


def mount_intelligence_routes(app: FastAPI) -> None:
    """Register all /api/intel/* routes on the FastAPI app."""

    # ── Model Catalog ─────────────────────────────────────────────

    @app.get("/api/intel/models")
    async def intel_models() -> list[dict[str, Any]]:
        return model_list_api()

    # ── Sessions ───────────────────────────────────────────────────

    @app.post("/api/intel/session")
    async def intel_session_create(request: Request) -> dict:
        body = await request.json()
        title = body.get("title", "")
        model = body.get("model", "glm-5.1")
        sid = _store.create_session(title=title, model=model)
        return {"session_id": sid, "title": title, "model": model}

    @app.get("/api/intel/session/{sid}")
    async def intel_session_get(sid: str) -> dict:
        session = _store.get_session(sid)
        if not session:
            raise HTTPException(404, "Session not found")
        return session

    @app.get("/api/intel/session/{sid}/messages")
    async def intel_session_messages(sid: str, limit: int = 200) -> list[dict]:
        messages = _store.get_messages(sid, limit=limit)
        return [m.to_dict() for m in messages]

    @app.delete("/api/intel/session/{sid}")
    async def intel_session_delete(sid: str) -> dict:
        ok = _store.delete_session(sid)
        if not ok:
            raise HTTPException(404, "Session not found")
        return {"deleted": True}

    # ── Chat Streaming (SSE) ───────────────────────────────────────

    @app.post("/api/intel/chat/stream")
    async def intel_chat_stream(request: Request) -> StreamingResponse:
        """Stream a chat completion with tool call handling.

        Creates a WorkflowTask for lifecycle management and broadcasts
        events to all subscribers (multi-tab support).
        """
        body = await request.json()
        session_id = body.get("session_id", "")
        model_id = body.get("model", "glm-5.1")
        user_message = body.get("message", "")
        provider = body.get("provider", {})  # Optional override

        if not session_id:
            raise HTTPException(400, "Missing session_id")
        if not user_message:
            raise HTTPException(400, "Missing message")

        # Create workflow task
        task = workflow_service.create_task(session_id=session_id, model=model_id)

        # Store user message
        _store.add_message(Message(
            role="user",
            content=user_message,
            model=model_id,
            session_id=session_id,
        ))

        # Create checkpoint before LLM call
        _rewind.auto_checkpoint(session_id, _store)

        # Get conversation history for API
        api_messages = _store.get_api_messages(session_id)

        # Get tool definitions for this model
        tools = _bridge.get_tool_definitions(model_id)

        # Resolve API credentials (vault Z.ai default)
        api_key, base_url = _resolve_credentials(provider)

        async def stream_chat() -> Any:
            url = f"{base_url}/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            req_body: dict[str, Any] = {
                "model": model_id,
                "messages": api_messages,
                "stream": True,
                "max_tokens": 16384,
            }
            if tools:
                req_body["tools"] = tools
                req_body["tool_choice"] = "auto"

            full_response = ""
            workflow_service.start_task(task.task_id)
            workflow_service.log_activity(
                task.task_id, f"Starting chat with {model_id}",
                category=ActivityType.INFO,
            )

            # Broadcast task_id so clients can subscribe
            yield _sse_format({"type": "task_start", "task_id": task.task_id, "session_id": session_id})

            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    async with client.stream("POST", url, json=req_body, headers=headers) as resp:
                        if resp.status_code != 200:
                            err = await resp.aread()
                            error_msg = f"HTTP {resp.status_code}: {err.decode()[:500]}"
                            event = SSEEvent.error(error_msg)
                            await workflow_service.broadcast(task.task_id, event)
                            yield _sse_format(event)
                            workflow_service.fail_task(task.task_id, error_msg)
                            return

                        # Track tool calls in progress
                        tool_calls_buffer: dict[int, dict[str, Any]] = {}

                        async for line in resp.aiter_lines():
                            # Check for cancellation
                            if task.is_cancelled:
                                event = SSEEvent.error("Task cancelled by user")
                                await workflow_service.broadcast(task.task_id, event)
                                yield _sse_format(event)
                                return

                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                # Process any remaining tool calls
                                if tool_calls_buffer:
                                    for tc in tool_calls_buffer.values():
                                        tool_name = tc.get("function", {}).get("name", "")
                                        tool_args_str = tc.get("function", {}).get("arguments", "{}")
                                        try:
                                            tool_args = json.loads(tool_args_str)
                                        except json.JSONDecodeError:
                                            tool_args = {}

                                        event = SSEEvent.tool_call(tool_name, tool_args)
                                        await workflow_service.broadcast(task.task_id, event)
                                        yield _sse_format(event)

                                        tc_result = await _bridge.execute(
                                            session_id=session_id,
                                            tool_name=tool_name,
                                            arguments=tool_args,
                                            model=model_id,
                                        )
                                        result_event = SSEEvent.tool_result(
                                            tool_name, tc_result.success,
                                            tc_result.distillation[:2000],
                                            tc_result.timing_ms,
                                        )
                                        await workflow_service.broadcast(task.task_id, result_event)
                                        yield _sse_format(result_event)

                                        workflow_service.log_activity(
                                            task.task_id,
                                            f"Tool: {tool_name} ({'ok' if tc_result.success else 'fail'})",
                                            category=ActivityType.TOOL_RESULT,
                                        )
                                break

                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            delta = chunk.get("choices", [{}])[0].get("delta", {})

                            # Handle content tokens
                            content = delta.get("content", "")
                            if content:
                                full_response += content
                                event = SSEEvent.token(content)
                                await workflow_service.broadcast(task.task_id, event)
                                yield _sse_format(event)

                            # Handle tool calls (streaming assembly)
                            if "tool_calls" in delta:
                                for tc_delta in delta["tool_calls"]:
                                    idx = tc_delta.get("index", 0)
                                    if idx not in tool_calls_buffer:
                                        tool_calls_buffer[idx] = {
                                            "id": tc_delta.get("id", ""),
                                            "function": {"name": "", "arguments": ""},
                                        }
                                    if tc_delta.get("id"):
                                        tool_calls_buffer[idx]["id"] = tc_delta["id"]
                                    fn = tc_delta.get("function", {})
                                    if fn.get("name"):
                                        tool_calls_buffer[idx]["function"]["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        tool_calls_buffer[idx]["function"]["arguments"] += fn["arguments"]

                                # Check if tool calls are complete (finish_reason)
                                finish = chunk.get("choices", [{}])[0].get("finish_reason")
                                if finish == "tool_calls":
                                    for tc in tool_calls_buffer.values():
                                        tool_name = tc.get("function", {}).get("name", "")
                                        tool_args_str = tc.get("function", {}).get("arguments", "{}")
                                        try:
                                            tool_args = json.loads(tool_args_str)
                                        except json.JSONDecodeError:
                                            tool_args = {}

                                        event = SSEEvent.tool_call(tool_name, tool_args)
                                        await workflow_service.broadcast(task.task_id, event)
                                        yield _sse_format(event)

                                        workflow_service.log_activity(
                                            task.task_id, f"Calling tool: {tool_name}",
                                            category=ActivityType.TOOL_CALL,
                                        )

                                        result = await _bridge.execute(
                                            session_id=session_id,
                                            tool_name=tool_name,
                                            arguments=tool_args,
                                            model=model_id,
                                        )

                                        result_event = SSEEvent.tool_result(
                                            tool_name, result.success,
                                            result.distillation[:2000],
                                            result.timing_ms,
                                        )
                                        await workflow_service.broadcast(task.task_id, result_event)
                                        yield _sse_format(result_event)

                                    tool_calls_buffer.clear()

                # Store assistant response
                if full_response:
                    _store.add_message(Message(
                        role="assistant",
                        content=full_response,
                        model=model_id,
                        session_id=session_id,
                    ))

                # Complete the task
                complete_event = SSEEvent.complete({"response_length": len(full_response)})
                await workflow_service.broadcast(task.task_id, complete_event)
                workflow_service.complete_task(task.task_id, {"response_length": len(full_response)})
                yield _sse_format(complete_event)

            except Exception as e:
                error_event = SSEEvent.error(str(e))
                await workflow_service.broadcast(task.task_id, error_event)
                workflow_service.fail_task(task.task_id, str(e))
                yield _sse_format(error_event)

            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_chat(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Task SSE Subscribe (multi-tab) ─────────────────────────────

    @app.get("/api/intel/task/{task_id}/stream")
    async def intel_task_stream(task_id: str) -> StreamingResponse:
        """Subscribe to a task's SSE event stream (for additional browser tabs)."""
        task = workflow_service.get_task(task_id)
        if not task:
            raise HTTPException(404, "Task not found")

        queue = workflow_service.subscribe(task_id)
        if not queue:
            raise HTTPException(404, "Task not found")

        async def event_stream() -> Any:
            try:
                while not task.is_finished():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        yield _sse_format(event)
                    except asyncio.TimeoutError:
                        # Send keepalive
                        yield ": keepalive\n\n"
                # Drain remaining events
                while not queue.empty():
                    try:
                        event = queue.get_nowait()
                        yield _sse_format(event)
                    except asyncio.QueueEmpty:
                        break
            finally:
                workflow_service.unsubscribe(task_id, queue)
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Task Management ────────────────────────────────────────────

    @app.get("/api/intel/tasks")
    async def intel_tasks_list(active_only: bool = False) -> list[dict]:
        if active_only:
            return [t.to_dict() for t in workflow_service.get_active_tasks()]
        return [t.to_dict() for t in workflow_service.get_recent_tasks()]

    @app.get("/api/intel/task/{task_id}")
    async def intel_task_get(task_id: str) -> dict:
        task = workflow_service.get_task(task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return task.to_dict()

    @app.post("/api/intel/task/{task_id}/cancel")
    async def intel_task_cancel(task_id: str) -> dict:
        ok = workflow_service.cancel_task(task_id)
        if not ok:
            raise HTTPException(400, "Task not found or already finished")
        return {"cancelled": True, "task_id": task_id}

    # ── User-in-the-Loop ───────────────────────────────────────────

    @app.post("/api/intel/task/{task_id}/interact")
    async def intel_task_interact(task_id: str, request: Request) -> dict:
        """Submit a user response to a pending interaction."""
        body = await request.json()
        action = body.get("action", "")
        data = body.get("data", {})

        if not action:
            raise HTTPException(400, "Missing action")

        ok = workflow_service.submit_interaction(task_id, action, data)
        if not ok:
            raise HTTPException(400, "Task not found or not waiting for interaction")
        return {"accepted": True, "task_id": task_id, "action": action}

    # ── Tool Call History ──────────────────────────────────────────

    @app.get("/api/intel/session/{sid}/tools")
    async def intel_tool_history(sid: str, limit: int = 100) -> list[dict]:
        return _store.get_tool_calls(sid, limit=limit)

    # ── Checkpoints / Rewind ───────────────────────────────────────

    @app.get("/api/intel/session/{sid}/checkpoints")
    async def intel_checkpoints_list(sid: str) -> list[dict]:
        return _rewind.list_checkpoints(sid)

    @app.post("/api/intel/checkpoint")
    async def intel_checkpoint_create(request: Request) -> dict:
        body = await request.json()
        sid = body.get("session_id", "")
        label = body.get("label", "manual")
        if not sid:
            raise HTTPException(400, "Missing session_id")
        messages = _store.get_api_messages(sid)
        tools = _store.get_tool_calls(sid)
        cp = _rewind.checkpoint(sid, messages, tools, label=label)
        return cp.to_dict()

    @app.post("/api/intel/restore/{cp_id}")
    async def intel_checkpoint_restore(cp_id: str) -> dict:
        messages = _rewind.restore_messages(cp_id)
        if messages is None:
            raise HTTPException(404, "Checkpoint not found")
        return {"restored": True, "checkpoint_id": cp_id, "message_count": len(messages)}

    @app.get("/api/intel/diff/{cp_a}/{cp_b}")
    async def intel_checkpoint_diff(cp_a: str, cp_b: str) -> dict:
        diff = _rewind.diff(cp_a, cp_b)
        if diff is None:
            raise HTTPException(404, "One or both checkpoints not found")
        return diff

    # ── Hooks ──────────────────────────────────────────────────────

    @app.get("/api/intel/hooks")
    async def intel_hooks_list() -> list[dict]:
        return _hooks.list_hooks()

    @app.post("/api/intel/hooks/{name}/toggle")
    async def intel_hooks_toggle(name: str, request: Request) -> dict:
        body = await request.json()
        enabled = body.get("enabled", True)
        ok = _hooks.toggle_hook(name, enabled)
        if not ok:
            raise HTTPException(404, f"Hook '{name}' not found")
        return {"hook": name, "enabled": enabled}

    # ── Stats ──────────────────────────────────────────────────────

    @app.get("/api/intel/stats")
    async def intel_stats() -> dict:
        store_stats = _store.get_stats()
        routing_stats = _router.get_routing_stats()
        return {
            "store": store_stats,
            "routing": routing_stats,
        }

