# The GLM Coding Harness — Feature Reference

## What This Is

A pure coding environment where GLM models and humans write software together. One surface. Chat-first. No ops dashboards, no governance panels, no system monitors, no cost trackers. You open it, you code.

Three user types share every feature equally:

| User Type | How They Access |
|---|---|
| **LLM Partners** (GLM models, autonomous agents) | `/api/intel/*` endpoints, SSE streams, tool_calls |
| **Human CLI Coders** | Terminal, `curl` to any endpoint, pipe SSE to `jq` |
| **Human GUI Coders** | Browser chat interface |

Default state: a conversation. You talk to GLM-5.1. It talks back. It runs tools. You approve. Code ships.

**Pure ZAI GLM visual identity.** Clean, spacious, typographic. Looks like Zhipu AI's product line because it is.

---

## Feature Catalog

### 1. Intelligence Layer

The GLM orchestration engine. Not a proxy — a conductor.

- **21 GLM models across 7 tiers**: Flagship (GLM-5.1, 200K context), Strong (GLM-5, GLM-5-Turbo), Fast (GLM-4.7-Flash series), Vision (GLM-4.6V, GLM-5V-Turbo), Audio, Image, OCR
- **Model Router**: GLM-5.1 decides who handles what. Quick questions → Flash. Code generation → GLM-5. Complex orchestration → stays with 5.1. Fallback chains if a model is slow or down
- **Real-time streaming**: Token-by-token SSE. No waiting for complete responses
- **Multi-tab**: Open 5 browser tabs, they all see the same stream simultaneously

### 2. Conversation Memory

SQLite-backed persistent history.

- **Sessions**: Create, title, switch, delete. Each session remembers its model preference
- **Full history**: Every message, every role (user/assistant/system/tool), timestamps, model attribution
- **Tool call log**: Every tool invocation with arguments, results, timing, success/failure
- **Survives restarts**: WAL mode for concurrent access. Multiple tabs, one database

### 3. Safety Gates (Hooks)

PEBKAC-style interceptors on every tool call.

- **destructive_gate**: Blocks `rm -rf`, `DROP TABLE`, etc. unless explicitly confirmed
- **path_jail**: Prevents file operations outside allowed directories
- **rate_limiter**: Caps tool call frequency to prevent runaway agents
- **output_validation**: Validates tool output before returning to the LLM
- **Toggle per-session**: Enable/disable any hook via API

### 4. Tool Bridge

Translates GLM tool_calls into terminal actions.

- **Progressive disclosure**: Flash models see 5 basic tools. GLM-5 sees 14. GLM-5.1 sees all 19
- **Model-aware**: Different models get different tool sets automatically
- **Distilled output**: Raw terminal output → structured results with timing data
- **Hook integration**: Every tool call passes through safety gates before and after execution

### 5. Rewind (Time Travel)

Checkpoint-and-restore for conversation state.

- **Auto-checkpoints**: Snapshot before every LLM call
- **Manual checkpoints**: Named snapshots at any point
- **Restore**: Roll back to any checkpoint, continue from there
- **Diff**: Compare any two checkpoints to see what changed
- **Branch**: Restore a checkpoint and take a different approach
- **Persistent**: SQLite-backed, survives restarts

### 6. Workflow Tasks

Async lifecycle for every streaming operation.

- **State machine**: pending → running → waiting_for_input → completed / error / cancelled
- **Multi-client broadcast**: All tabs receive identical events in real-time
- **Cancellation**: Cancel mid-stream. Propagates to the LLM, tool execution, all subscribers
- **Activity log**: Structured log of every event, persisted to SQLite
- **Task management**: List active tasks, check status, cancel, view activity history

### 7. Human-in-the-Loop

The LLM pauses and asks before proceeding.

- **Interaction types**: Requirement questions, plan review, destructive confirmations, path choices
- **Async pause**: The task literally pauses. SSE stream sends an `interaction` event
- **Response**: Human responds via chat UI, CLI, or API. Task resumes with response injected
- **Cancel-safe**: If cancelled while waiting, unblocks cleanly. No deadlocks

### 8. Workspace (File I/O)

Filesystem access for the code you're writing.

- **Browse**: Navigate directories with metadata
- **Read/Write**: Open any file, save any file, create directories
- **Code editor**: In-browser editing with syntax support
- **Static serving**: Browser-safe file access for preview

### 9. Credentials (Vault)

API key storage so keys never appear in prompts or tool arguments.

- **Store/Retrieve/Delete**: Named secrets, encrypted at rest
- **Three-tier resolution**: Explicit inline → vault ID lookup → Z.ai default
- **Multi-provider**: Store keys for multiple LLM providers. Router selects the right one

---

## SSE Event Protocol

| Event | Payload | Purpose |
|---|---|---|
| `task_start` | `{task_id, session_id}` | New task created |
| `token` | `{content}` | Streaming LLM token |
| `progress` | `{percent, message}` | Task progress |
| `tool_call` | `{tool, args}` | LLM invoking a tool |
| `tool_result` | `{tool, success, result, timing_ms}` | Tool finished |
| `activity` | `{message, category}` | Structured log |
| `interaction` | `{interaction_type, title, description, data, options, required}` | LLM asking for input |
| `complete` | `{result}` | Task succeeded |
| `error` | `{error}` | Something broke |
| `keepalive` | (empty) | Connection alive |

---

## What This Is NOT

- Not an ops dashboard
- Not a governance tool
- Not a system monitor
- Not a cost tracker
- Not an agent lifecycle manager
- Not a multi-terminal orchestrator

It's where you write code with GLM. That's the whole product.

