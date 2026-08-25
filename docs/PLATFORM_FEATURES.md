# The Kernel Platform — Complete Feature Reference

## Design Principle: Three Equal Citizens

Every feature described below is a **primary feature** for every user type. There are no "power user" features hidden behind expert-mode toggles, and there are no dumbed-down interfaces for GUI users. The same API surface, the same intelligence layer, the same tool bridge — whether you're an LLM agent orchestrating a 12-step build, a human writing code in a terminal, or a human clicking through a browser.

| User Type | How They Access The Same Features |
|---|---|
| **LLM Partners** (GLM models, autonomous agents) | API calls to `/api/intel/*`, `/api/do`, SSE streams, tool_calls |
| **Human CLI Coders** (terminal-first, keyboard-driven) | Terminal tabs, WebSocket PTY, `/api/fs/*` file operations, curl to any endpoint |
| **Human GUI Coders** (browser-first, visual workflows) | The chat interface, workspace editor, file browser, visual project governance |

The UI's default resting state is a **chat interface** — because that's what GUI coders expect, and because an LLM-native platform should look like it talks to you. Underneath that chat surface sits every feature listed below, accessible through natural language, keyboard shortcuts, or direct API calls depending on the user's preference.

---

## Visual Identity

The Kernel's UI is **pure, unabashed ZAI GLM** — the visual language of Zhipu AI's product line. Not inspired by. Not reminiscent of. **The real thing.** Clean, spacious, typographic confidence. The kind of interface that says "intelligent system" before a single word is typed.

The default view is a conversation. The user opens the browser and sees a chat interface — greeting, input bar, model selector. This is not a limitation. It's the front door. Every other feature — terminals, editors, agents, governance — is a tab away, but the chat is home base because that's how you talk to GLM-5.1 and that's how GLM-5.1 talks to you.

---

## Feature Catalog

### 1. Intelligence Layer (GLM Orchestration)

The core differentiator. The Kernel doesn't just proxy LLM calls — it **orchestrates** them with a three-model architecture.

**What it is:** A complete intelligence pipeline that sits between the user (human or LLM) and Z.ai's GLM model family.

**What it does:**
- **Model Registry** — 21 GLM models across 7 tiers: Flagship (GLM-5.1), Strong (GLM-5, GLM-5-Turbo), Fast (GLM-4.7-Flash series), Vision (GLM-4.6V, GLM-5V-Turbo), Audio (GLM-ASR), Image (GLM-Image, CogView-4), Tool (GLM-OCR)
- **Model Router** — GLM-5.1 acts as the conductor. When a task comes in, the router decides which model handles it. Simple questions go to Flash. Code generation goes to GLM-5. Complex orchestration stays with 5.1. The router maintains fallback chains — if a model is slow or down, the next-strongest candidate takes over
- **Streaming SSE** — Real-time token-by-token streaming via `/api/intel/chat/stream`. Every response streams as it generates. No waiting for complete responses
- **Multi-Tab Support** — Open the same task in 5 browser tabs, they all see the same stream. The WorkflowService broadcasts every token, tool call, and interaction event to all subscribers simultaneously

**Who uses it equally:**
- LLM Partners: Call `/api/intel/chat/stream` with tool definitions, get back SSE events including tool_call and tool_result
- CLI Coders: `curl` the same endpoint, pipe SSE to `jq`, or use it from scripts
- GUI Coders: Type in the chat box, watch tokens appear, click "approve" when the agent asks permission

---

### 2. Conversation Store (Session Memory)

**What it is:** SQLite-backed persistent memory for every conversation, message, tool invocation, and checkpoint.

**What it does:**
- **Sessions** — Create, list, retrieve, delete chat sessions. Each session has a title, model preference, and UUID
- **Messages** — Full conversation history with roles (user/assistant/system/tool), timestamps, and model attribution
- **Tool Call History** — Every tool invocation is logged with arguments, results, timing, and success/failure status
- **WAL Mode** — Write-Ahead Logging for concurrent read/write without blocking. Multiple tabs, multiple agents, one database
- **Cross-Session Queries** — Look up tool calls by session, get message history with pagination, retrieve session statistics

**Who uses it equally:**
- LLM Partners: Retrieve conversation history to maintain context across long autonomous sessions
- CLI Coders: Query session history from scripts, audit tool call patterns
- GUI Coders: Scroll back through chat history, see what the agent did and when

---

### 3. Hook Engine (Safety Gates)

**What it is:** A PEBKAC-style pre/post hook system that intercepts every tool call before and after execution.

**What it does:**
- **4 Built-in Hooks:**
  - `destructive_gate` — Blocks commands matching dangerous patterns (rm -rf, DROP TABLE, etc.) unless explicitly confirmed
  - `path_jail` — Prevents file operations outside allowed directories
  - `rate_limiter` — Caps tool call frequency to prevent runaway agents
  - `output_validation` — Validates tool output before returning it to the LLM
- **Toggle Control** — Enable or disable any hook per-session via `/api/intel/hooks/{name}/toggle`
- **Pre/Post Phases** — Hooks fire before execution (can block) and after execution (can modify/validate output)
- **Extensible** — New hooks can be added to the engine without modifying core code

**Who uses it equally:**
- LLM Partners: Hooks prevent autonomous agents from accidentally destroying things. The agent gets a structured error it can reason about
- CLI Coders: Safety net for automated scripts — `destructive_gate` catches the 3am `rm -rf /tmp/*` that was supposed to be `rm -rf /tmp/build/*`
- GUI Coders: When a hook blocks something, the chat interface shows a clear explanation and asks for confirmation

---

### 4. Tool Bridge (ATerm Integration)

**What it is:** The translation layer between GLM tool_calls and ATerm's `/api/do` terminal actions.

**What it does:**
- **Progressive Disclosure** — Three tiers of tool visibility:
  - Tier 1 (5 tools): Basic — list, create, run, read, stop — for small models
  - Tier 2 (14 tools): Full — start, cancel, answer, delete, note, search, broadcast, history, checkpoint, record, verify
  - Tier 3 (19 tools): Extended — batch, automate, bridge — for flagship models orchestrating complex workflows
- **Model-Aware Selection** — The bridge returns different tool sets depending on which GLM model is asking. Flash gets Tier 1. GLM-5 gets Tier 2. GLM-5.1 gets everything
- **Execution + Distillation** — When a tool call executes, the bridge runs it through ATerm, distills the output (5 modes from raw to summary), and returns a structured result with timing data
- **Hook Integration** — Every tool call passes through the Hook Engine before and after execution

**Who uses it equally:**
- LLM Partners: Select tools via `tool_choice: "auto"`, let the bridge handle ATerm communication
- CLI Coders: Direct `/api/do` calls — the same actions the bridge uses are available as raw API
- GUI Coders: The chat interface renders tool calls as collapsible cards showing what ran, what it returned, and how long it took

---

### 5. Rewind System (Time Travel)

**What it is:** Checkpoint-and-restore for conversation state. Go back to any point in a session and branch from there.

**What it does:**
- **Auto-Checkpoints** — Before every LLM call, the system snapshots the conversation state
- **Manual Checkpoints** — Create named checkpoints at any time via `/api/intel/checkpoint`
- **Restore** — Roll back to any checkpoint, replacing the current conversation state
- **Diff** — Compare any two checkpoints to see exactly what changed between them
- **Branch** — Restore a checkpoint and continue the conversation from that point, creating an alternate timeline
- **SQLite-Backed** — All checkpoints persist across restarts

**Who uses it equally:**
- LLM Partners: Autonomous agents checkpoint before risky operations, restore if something goes wrong
- CLI Coders: Script checkpoint/restore sequences for automated workflows
- GUI Coders: Visual timeline showing conversation checkpoints. Click to jump back, see what changed, branch into a different approach

---

### 6. Workflow Task System (Async Lifecycle)

**What it is:** Production-grade async task management for every streaming operation.

**What it does:**
- **Task State Machine** — Every chat completion creates a WorkflowTask with lifecycle states: pending → running → waiting_for_input → completed / error / cancelled
- **Multi-Client Broadcast** — Multiple browser tabs or agents subscribe to the same task and receive identical SSE event streams in real-time
- **Cancellation** — Any task can be cancelled mid-stream. The cancel propagates to the LLM stream, tool execution, and all subscribers
- **Activity Logging** — Structured log of every significant event: model selection, tool calls, tool results, errors, user interactions
- **Task Management API** — List active tasks, get task status, cancel tasks, view activity logs

**Who uses it equally:**
- LLM Partners: Monitor autonomous task progress, cancel runaway operations
- CLI Coders: Track multiple concurrent sessions from the terminal
- GUI Coders: Progress indicators, activity feeds, cancel buttons in the chat UI

---

### 7. User-in-the-Loop (Human Approval)

**What it is:** The mechanism for an LLM to pause mid-task and ask a human for input before continuing.

**What it does:**
- **Interaction Types:** Requirement questions (the LLM needs clarification), plan review (here's what I'm about to do, approve it), confirmations (this is destructive, are you sure?), generic options (pick one of these paths)
- **Async Pause** — The LLM's streaming task literally pauses. It enters `waiting_for_input` state. The SSE stream sends an `interaction` event to all subscribers
- **Human Response** — The human (via chat UI, CLI, or API call) submits their response. The task resumes with the response injected into the LLM's context
- **Cancel Compatibility** — If the task is cancelled while waiting for input, the pause unblocks cleanly. No deadlocks

**Who uses it equally:**
- LLM Partners: Autonomous agents use this to gate destructive operations, ask for clarification when requirements are ambiguous, and present plans for approval
- CLI Coders: The interaction event appears in the SSE stream. Respond via `curl POST /api/intel/task/{id}/interact`
- GUI Coders: Beautiful inline cards in the chat — "The agent wants to run `rm -rf ./build`. Approve?" with buttons

---

### 8. Terminal System (PTY over WebSocket)

**What it is:** Real, full-fidelity pseudo-terminals streamed over WebSocket and rendered in the browser.

**What it does:**
- **Real PTY** — Not a simulation. Actual Unix pseudo-terminals with full shell support, colors, control characters, interactive programs
- **Session Persistence** — Terminal sessions survive reconnections. Reconnect to a running PTY from any tab
- **Agent Terminals** — Named, tagged, auto-starting terminal sessions for agent execution. Each agent gets its own PTY with its own working directory and environment
- **Dual Console** — Side-by-side terminal panes for when one terminal is never enough
- **ATerm Intelligence** — Terminals powered by ATerm's semantic state detection. The system knows when a terminal is `ready`, `busy`, `waiting_for_input`, `error`, or `exited`

**Who uses it equally:**
- LLM Partners: Spawn terminals, send commands, read output, detect state — all through `/api/do` tool calls
- CLI Coders: Direct PTY access. The same terminals they'd use in any terminal emulator
- GUI Coders: xterm.js-rendered terminal panels in the browser. Click, type, scroll — it's a real terminal

---

### 9. Workspace System (File Operations)

**What it is:** Full filesystem access through the API — browse, read, write, create, rename, delete.

**What it does:**
- **Directory Browsing** — Navigate the filesystem with metadata (sizes, permissions, timestamps)
- **File I/O** — Read any file, write any file, create directories, rename, move, delete
- **Static File Serving** — `/api/fs/serve` replaces `file://` links for browser-safe file access
- **Code Editor** — MWIDE-based code editor injected directly into the DOM for in-browser file editing
- **Agentic Web-Build API** — Full workspace management for autonomous agents: create workspaces, batch file operations, diff previews, job execution, preview servers, deployment records, browser verification sessions, quality gates

**Who uses it equally:**
- LLM Partners: `/api/fs/*` for file operations, `/api/v1/*` for structured web-build workflows
- CLI Coders: curl the file API or use the Workspace tab for visual browsing
- GUI Coders: File browser sidebar, code editor tab, drag-and-drop uploads

---

### 10. Vault System (Credential Management)

**What it is:** Encrypted-at-rest secret storage for API keys and credentials.

**What it does:**
- **Store/Retrieve/Delete** — Named secrets stored in `~/.config/keys.json`
- **Provider Resolution** — Three-tier credential resolution: (1) explicit inline apiKey+baseUrl, (2) vault ID lookup, (3) default Z.ai key
- **LLM Integration** — The intelligence layer's credential resolution automatically checks the vault before requesting keys
- **Multi-Provider Support** — Store keys for multiple providers. The model router selects the right credentials based on the target model

**Who uses it equally:**
- LLM Partners: No API keys in prompts or tool arguments. Everything resolved server-side from the vault
- CLI Coders: `curl -X POST /api/vault/set -d '{"id":"zai","key":"YOUR_KEY"}'`
- GUI Coders: Settings panel with masked key inputs and connection test buttons

---

### 11. Project Governance

**What it is:** Project-level dashboard for scanning, tagging, quarantining, and managing codebases.

**What it does:**
- **Project Registry** — Scan and list all registered projects
- **Tagging** — Organize projects with tags, rename tags, filter by tag
- **Quarantine** — Flag problematic files or documents, generate quarantine reports
- **Bootstrap/Finisher** — Dispatch project-specific make targets for setup and verification
- **Layouts** — Save and restore UI layouts per project

**Who uses it equally:**
- LLM Partners: API-driven project scanning and health checks
- CLI Coders: `curl /api/projects` for project status, `POST /api/projects/{name}/finisher` for verification
- GUI Coders: Visual dashboard with project cards, tag filters, quarantine indicators

---

### 12. System Health & Infrastructure

**What it is:** Real-time monitoring of the system the Kernel runs on.

**What it does:**
- **Health Scanning** — CPU, memory, disk, network, process monitoring
- **Rescan** — Trigger fresh scans on demand
- **Performance Metrics** — `/api/performance` endpoint for detailed runtime statistics
- **System Map** — Infrastructure cartography rendered in Shadow DOM. Visual representation of what's running, what's connected, what's failing

**Who uses it equally:**
- LLM Partners: Check system health before resource-intensive operations
- CLI Coders: `curl /api/system-health | jq` for scriptable health checks
- GUI Coders: Visual health dashboard with color-coded indicators

---

### 13. Agent Lifecycle Management

**What it is:** Full CRUD for persistent agent instances with PTY-backed execution.

**What it does:**
- **Agent CRUD** — Create, list, get, update, delete agents with names, directories, commands, environments, tags, and pinning
- **Lifecycle Control** — Start, stop, restart, resize agent terminals
- **Bulk Operations** — Restart, stop, or start all agents at once
- **Broadcast** — Send a message to all running agent terminals simultaneously
- **Templates** — Save agent configurations as reusable templates
- **Import/Export** — Export all agent configurations, import them on another machine
- **Launchd Integration** — Agents can be configured with macOS launchd persistence (timer, hook, keepalive)
- **Scrollback** — Retrieve historical terminal output with pagination and statistics

**Who uses it equally:**
- LLM Partners: Create agents programmatically, orchestrate multi-agent workflows
- CLI Coders: `curl POST /api/agents` to spin up a new agent from a script
- GUI Coders: Agent management panel with start/stop buttons, status indicators, terminal preview

---

### 14. Agent Browser (Floyd Vision)

**What it is:** A browser runtime specifically designed for AI agents to navigate, inspect, and verify web content.

**What it does:**
- **CDP-Enabled** — Chrome DevTools Protocol for full browser automation
- **Kernel Command Deck** — Browser extension that wires the agent browser to Kernel APIs
- **Live Session Verification** — Verify that browser evidence is from a live session, not a cached contract-mode response
- **Browser API Surface** — Navigate, snapshot, screenshot, console capture, network monitoring, JavaScript evaluation, accessibility audit — all through `/api/v1/browser/*`
- **Quality Gates** — Run automated quality checks against web content using command and browser verification

**Who uses it equally:**
- LLM Partners: Full browser automation for testing, verification, and web interaction
- CLI Coders: Launch with `make agent-browser`, script via the browser API
- GUI Coders: Visual browser panel with agent overlays and verification results

---

### 15. LLM Proxy (Legacy)

**What it is:** The original LLM streaming proxy, predating the Intelligence Layer.

**What it does:**
- **SSE Proxy** — `/api/llm/stream` proxies OpenAI-compatible SSE streams to any LLM provider
- **Connectivity Test** — `/api/llm/test` verifies that API keys work and the provider is reachable
- **Vault Integration** — Keys resolved from the vault system

**Who uses it equally:**
- LLM Partners: Direct LLM access without the intelligence layer's routing/hooks
- CLI Coders: `curl POST /api/llm/stream` for raw LLM calls
- GUI Coders: Powers the legacy LLM integration tab

---

### 16. Git Proxy

**What it is:** A transparent proxy for git operations.

**What it does:**
- Forwards any git operation through `ANY /api/git-proxy/{path}`
- Enables git access from environments where direct git connectivity is restricted

---

### 17. Spend Watch

**What it is:** Cost tracking for LLM API usage and infrastructure spend.

**What it does:**
- Tracks where the money goes across LLM calls, infrastructure, and services
- Visual dashboard for spending patterns
- Alert thresholds for budget overruns

---

### 18. Mac Cleanup

**What it is:** System maintenance reports for macOS.

**What it does:**
- `/api/autoclean` — Trigger system cleanup
- `/api/autoclean/status` — Check cleanup progress
- Identifies cache directories, stale builds, temporary files

---

## SSE Event Protocol

The streaming protocol between the Kernel and all clients (browser, CLI, LLM):

| Event Type | Direction | Payload | Purpose |
|---|---|---|---|
| `task_start` | Server → Client | `{task_id, session_id}` | New task created, clients can subscribe |
| `token` | Server → Client | `{content}` | Streaming LLM token |
| `progress` | Server → Client | `{percent, message}` | Task progress update |
| `tool_call` | Server → Client | `{tool, args}` | LLM is invoking a tool |
| `tool_result` | Server → Client | `{tool, success, result, timing_ms}` | Tool execution completed |
| `activity` | Server → Client | `{message, category}` | Structured activity log entry |
| `interaction` | Server → Client | `{interaction_type, title, description, data, options, required}` | LLM is asking for human input |
| `complete` | Server → Client | `{result}` | Task finished successfully |
| `error` | Server → Client | `{error}` | Something broke |
| `keepalive` | Server → Client | (empty) | SSE connection is alive |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        THE KERNEL (port 11527)                      │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Chat UI     │  │ Terminal Tabs │  │ Workspace Editor         │  │
│  │  (default)   │  │ (PTY/xterm)  │  │ (file browser/editor)    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────────┘  │
│         │                 │                      │                   │
│         └────────────┬────┴──────────────────────┘                   │
│                      │                                               │
│         ┌────────────▼────────────┐                                  │
│         │    Intelligence Layer    │                                  │
│         │                          │                                  │
│         │  ┌────────────────────┐  │                                  │
│         │  │  Model Router      │  │  GLM-5.1 (conductor)           │
│         │  │  (task → model)    │  │  GLM-5    (coding)             │
│         │  └────────┬───────────┘  │  GLM-4.7-Flash (fast tools)   │
│         │           │              │  + 18 more models               │
│         │  ┌────────▼───────────┐  │                                  │
│         │  │  Hook Engine       │  │  destructive_gate               │
│         │  │  (safety gates)    │  │  path_jail                      │
│         │  └────────┬───────────┘  │  rate_limiter                   │
│         │           │              │  output_validation              │
│         │  ┌────────▼───────────┐  │                                  │
│         │  │  Tool Bridge       │  │  Tier 1: 5 tools (Flash)        │
│         │  │  (GLM → ATerm)    │  │  Tier 2: 14 tools (GLM-5)       │
│         │  └────────┬───────────┘  │  Tier 3: 19 tools (GLM-5.1)    │
│         │           │              │                                  │
│         │  ┌────────▼───────────┐  │                                  │
│         │  │  Conversation Store│  │  Sessions, Messages, Tool Calls  │
│         │  │  (SQLite WAL)      │  │  Checkpoints, Rewind             │
│         │  └────────────────────┘  │                                  │
│         │                          │                                  │
│         │  ┌────────────────────┐  │                                  │
│         │  │  Workflow Tasks    │  │  Async lifecycle                 │
│         │  │  (SSE broadcast)   │  │  Multi-tab, Cancel, Interact    │
│         │  └────────────────────┘  │                                  │
│         └────────────┬────────────┘                                  │
│                      │                                               │
│         ┌────────────▼────────────┐                                  │
│         │  ATerm (/api/do)         │  20 terminal actions             │
│         │  (Self-Aware Terminal)   │  Semantic state detection        │
│         │                          │  5 distillation modes            │
│         │                          │  SQLite persistence               │
│         └─────────────────────────┘                                   │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │  Vault       │  │  Governance  │  │  Git Proxy   │               │
│  │  (secrets)   │  │  (projects)  │  │  (ops)       │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │  Health      │  │  Agent CRUD  │  │  Floyd Vision│               │
│  │  (monitoring)│  │  (lifecycle) │  │  (browser)   │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

---

*Every feature listed above is equally accessible to LLM partners via API, human CLI coders via curl/WebSocket, and human GUI coders via the browser interface. The chat interface is the default home. Everything else is one tab away.*

