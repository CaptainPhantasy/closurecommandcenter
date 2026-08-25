"""
Intelligence Layer — the "driver's seat" between GLM models and ATerm MCP tools.

Architecture:
  Z.ai Client → Model Router → Hook Engine → Tool Bridge → ATerm /api/do
                                 ↕
                            Rewind System
                                 ↕
                          Conversation Store

Modules:
	models          — GLM model registry (20+ models across 7 tiers)
	model_router    — GLM-5.1 conductor, routes tasks to optimal model
	hook_engine     — PEBKAC-style mechanical hooks (pre/post tool call gates)
	tool_bridge     — Maps GLM tool_calls to ATerm MCP actions (progressive disclosure)
	rewind          — Time-travel checkpoint system (SQLite-backed)
	conversation    — Message history, tool call logs, session management
	workflow        — Async task lifecycle, multi-client SSE broadcast, user-in-the-loop

Usage:
	from modules.intelligence import (
			ConversationStore, HookEngine, ModelRouter, ToolBridge, RewindSystem,
			WorkflowService, WorkflowTask, SSEEvent,
			get_tools_for_model, model_list_api,
	)
"""

__version__ = "0.1.0"

from .conversation import ConversationStore, Message, ToolCall
from .hook_engine import HookEngine, Hook, HookPhase, HookResult, HookContext
from .model_router import ModelRouter, TaskCategory, RoutingDecision
from .tool_bridge import ToolBridge, BridgeResult, get_tools_for_model
from .rewind import RewindSystem, Checkpoint
from .models import model_list_api, get_model, get_chat_models, MODELS, GLMModel
from .workflow import WorkflowService, WorkflowTask, SSEEvent, ActivityType, TaskStatus, workflow_service, load_activities

__all__ = [
		"ConversationStore", "Message", "ToolCall",
		"HookEngine", "Hook", "HookPhase", "HookResult", "HookContext",
		"ModelRouter", "TaskCategory", "RoutingDecision",
		"ToolBridge", "BridgeResult", "get_tools_for_model",
		"RewindSystem", "Checkpoint",
		"WorkflowService", "WorkflowTask", "SSEEvent", "ActivityType", "TaskStatus", "workflow_service", "load_activities",
		"model_list_api", "get_model", "get_chat_models", "MODELS", "GLMModel",
]

