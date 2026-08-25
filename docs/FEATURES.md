# The Kernel — Technical Feature Reference

## Or: Everything It Does (And What It's Saving For Later)

**DOCUMENT CLASSIFICATION:** Technical Reference / Factual
**DATE RECORDED:** 2026-05-11
**PRODUCT:** Floyd's Unified Command Kernel
**KERNEL PORT:** 11527

---

## Overview

The Kernel is a single monoapplication. It consolidates seven Legacy AI capabilities into one FastAPI backend with a zero-build vanilla JavaScript frontend, served from port 11527. Every module lives inside the Kernel's `modules/` directory as copied-and-adapted source — not iframes, not adapters, not a launcher pretending to be integration.

This document covers every feature the Kernel ships: what's live, what's wired but stubbed, and what's latent infrastructure waiting for a consumer.

---

## Active Features

### Backend

| Feature | Detail |
|---|---|
| **Runtime** | Python 3.14, uvicorn, launchd-managed (`~/Library/LaunchAgents/com.legacyai.tcc.plist`) |
| **Framework** | FastAPI with async handlers |
| **Total Python lines** | 11,599 (core files, excluding `.venv`) |
| **Test suite** | 201 collected tests; Playwright-gated UI/proof tests require the `playwright` Python package |
| **WebSocket endpoints** | 4 (PTY, collab, events, agent catch-all) |
| **REST API routes** | 30+ endpoints across 8 domain groups |

### REST API Surface

#### Governance (`/api/projects`)
- `GET /api/projects` — scan and list all registered projects
- `GET /api/quarantine-summary` — quarantined document report
- `GET /api/tags` — project tag index
- `POST /api/projects/{name}/bootstrap` — dispatch bootstrap make targets
- `POST /api/projects/{name}/finisher` — run completion verification

#### Closure Control (`/api/closure`)
- `GET /api/closure/board` — closure-leverage-ranked outcome board with CLOSED WHEN / NOW / NEXT / FINAL projections
- `POST /api/closure/projects` — ratify a durable outcome contract with distinct proposer and ratifier identities
- `POST /api/closure/actions/{id}/claim` — bounded, idempotent worker claim or same-attempt approval resume
- `POST /api/closure/actions/{id}/result` — record attempt-bound worker evidence and typed obstacle classification
- `POST /api/closure/actions/{id}/verify` — independent, attempt/result-receipt-bound verdict; workers cannot verify themselves
- `POST /api/closure/actions/{id}/effects/reserve` — reserve a server-hashed effect descriptor through the approval/budget gate; this narrow port records authority but does not execute the external effect
- `POST /api/closure/effects/{key}/status` — adapter-owned `RESERVED → DISPATCHED → COMPLETED | FAILED | UNKNOWN` reconciliation with terminal evidence
- `POST /api/closure/effects/{key}/reconcile` — explicit operator decision for an `UNKNOWN` may-have-applied effect; it is never auto-redriven
- `POST /api/closure/projects/{id}/halt` — durable operator containment
- `GET /api/closure/projects/{id}/receipts` — tamper-evident receipt history
- `GET /api/closure/health` — mechanism, control, workflow, and outcome health layers
- `POST /api/closure/self-test` — five adversarial runs covering ambiguity, local tool loss, late approval, restart/duplicate effects, and a lying worker

Closure state is schema-versioned and SQLite/WAL-backed, with serialized legacy migrations that conservatively mark pre-lifecycle effects `UNKNOWN`. It is distinct from the filesystem Project Control inventory. A project is shown as truly blocked only when no runnable path remains and every still-open path has structured hard/approval evidence. Health cross-checks mutable counters and pass states against the append-only receipt chain, so a direct database state flip degrades control health instead of becoming completion.

Mutation endpoints are loopback-only by default. Remote mode requires both `FLOYD_CLOSURE_TOKEN` / `X-Floyd-Closure-Token` and a `FLOYD_CLOSURE_PRINCIPALS_JSON` registry. Create calls carry distinct proposer and ratifier credential headers; other mutations carry `X-Floyd-Closure-Principal-Token`, which must match the claimed worker, verifier, adapter, or operator and route role. Registry tokens must be unique. Approval-bearing calls additionally require a credential distinct from every perimeter/role credential via `FLOYD_CLOSURE_APPROVAL_TOKEN` / `X-Floyd-Closure-Approval-Token`. Health reports whether scoped credentials or the local loopback trust boundary is active. Runtime conformance intentionally remains `UNSCORED` until external acceptance evidence is available.

#### Filesystem (`/api/fs`)
- `GET /api/fs/home` — user home directory path
- `GET /api/fs/list` — directory listing
- `GET /api/fs/read` — file content reader
- `POST /api/fs/write` — file writer
- `POST /api/fs/mkdir` — directory creation
- `POST /api/fs/rename` — rename/move
- `POST /api/fs/remove` — delete
- `GET /api/fs/stat` — file metadata
- `GET /api/fs/serve` — static file serve (replaces `file://` links)
- `POST /api/fs/browse` — directory browser

#### Vault (`/api/vault`)
- `GET /api/vault/list` — list stored secrets
- `POST /api/vault/set` — store a secret
- `POST /api/vault/delete` — remove a secret

#### ATerm Agent Actions (`POST /api/do`)
20 actions accepted via a single POST endpoint. Full ATerm functionality is native to the Kernel API; only the legacy browser bridge is intentionally status-only because Floyd Vision owns browser control.

#### Health
- `GET /api/system-health` — current system health snapshot
- `GET /api/system-health/rescan` — trigger fresh scan

#### LLM Proxy
- `POST /api/llm/stream` — streaming LLM proxy (DeepSeek via vaulted key)
- `POST /api/llm/test` — connectivity test

#### Git Proxy
- `ANY /api/git-proxy/{path}` — forward git operations

#### Agents
- `GET /api/agents` — list registered agents with live status
- `GET /api/agents/{id}` — single agent detail
- `POST /api/agents/{id}/restart` — restart a running agent

#### Agent Browser (`modules/agent-browser/source`)
- `make agent-browser` — launches Floyd Vision as the Kernel default browser runtime with CDP enabled and the Kernel Command Deck extension loaded.
- Runtime defaults: Kernel URL `http://127.0.0.1:11527`, CDP port `9005`, browser MCP/server port `9105`, extension port `9305`.
- Overrides: `FLOYD_VISION_BROWSER_BINARY` selects the browser executable; `FLOYD_VISION_START_URL` selects the initial Kernel URL; `FLOYD_VISION_CDP_URL` selects the CDP discovery base URL for `/api/v1/browser/sessions` live checks.
- Live verification: browser session requests may set `requireLive: true`; the API verifies `GET $FLOYD_VISION_CDP_URL/json/version` and returns `floyd_vision_unavailable` instead of pretending contract-mode responses are live browser evidence.

#### Agentic Web-Build API (`/api/v1`)
- `POST /api/v1/workspaces` — create/register a workspace root for autonomous web builds.
- `GET /api/v1/workspaces` and `GET /api/v1/workspaces/{workspace_id}` — list and inspect registered workspaces.
- `GET /api/v1/workspaces/{workspace_id}/files` — list files under a workspace.
- `GET /api/v1/workspaces/{workspace_id}/files/content` — read a workspace file with an etag.
- `PUT /api/v1/workspaces/{workspace_id}/files/content` — create or replace a workspace file.
- `POST /api/v1/workspaces/{workspace_id}/files:batch` — apply mkdir/write/delete operations in one request.
- `POST /api/v1/workspaces/{workspace_id}/files:diff` — preview file changes before writing.
- `POST /api/v1/workspaces/{workspace_id}/jobs`, `GET /api/v1/jobs/{job_id}`, `GET /api/v1/jobs/{job_id}/logs`, `DELETE /api/v1/jobs/{job_id}` — run, inspect, stream logs for, and cancel command jobs.
- `POST /api/v1/workspaces/{workspace_id}/preview`, `GET /api/v1/previews/{preview_id}`, `DELETE /api/v1/previews/{preview_id}` — manage preview server records.
- `POST /api/v1/browser/sessions` plus `/navigate`, `/snapshot`, `/screenshot`, `/console`, `/network`, `/evaluate`, and `/audit` — create browser verification sessions and collect page evidence.
- `POST /api/v1/workspaces/{workspace_id}/deployments`, `GET /api/v1/deployments/{deployment_id}`, `POST /api/v1/deployments/{deployment_id}:verify` — create and verify deployment records.
- `POST /api/v1/workspaces/{workspace_id}/quality-gates`, `GET /api/v1/quality-gates/{run_id}` — run command/browser quality gates for autonomous completion checks.

### WebSocket Endpoints

| Endpoint | Protocol | Purpose | Status |
|---|---|---|---|
| `/ws/pty` | JSON-PTY (MWIDE TerminalPane) | Real PTY spawn/stream with session resume | **Live** |
| `/ws/collab` | Room-based fan-out (MWIDE collab.ts) | Real-time document sync | **Live** |
| `/ws/events` | Push events (ATerm useEvents.ts) | Session lifecycle broadcasting | **Live** |
| `/ws/{agent_id}` | Raw PTY bytes (catch-all) | Sidebar agent terminals + ATerm session terminals | **Live** |

### ATerm Actions (`POST /api/do`)

**Live (functional Kernel-native ATerm surface):**

| Action | Behavior |
|---|---|
| `list` | List all ATerm sessions with status, history count, and checkpoint count |
| `create` | Create a named session, optionally auto-starting a real PTY |
| `delete` | Destroy a session and clean up session-scoped automation jobs |
| `start` | Spawn a real PTY process in the session |
| `stop` | Kill the PTY process |
| `cancel` | Interrupt the running PTY |
| `run` / `answer` | Send input to the PTY, record command history, emit output events |
| `read` | Read bounded PTY output buffer |
| `note` | Attach or read a session scratchpad note |
| `history` | Return bounded per-session command history |
| `search` | Regex search live session output buffers |
| `broadcast` | Send one input to all ready sessions or a selected session list |
| `status` | Return semantic state for every session |
| `checkpoint` | Save, list, and restore session output/history/scratchpad/directory/env state |
| `record` | Start, stop, list, and fetch session event recordings |
| `verify` | Run a verification command and return structured pass/fail state |
| `batch` | Run up to 20 supported session actions in one request |
| `automate` | Register, list, and cancel cron-shaped automation metadata |
| `bridge` | Return explicit legacy browser bridge status; not the primary browser-control path |

`/ws/events` supports lifecycle event fan-out with subscribe/unsubscribe filters by session and event type. ATerm MCP is wired through `modules/agent-execution/source/src/mcp/server.ts` and launchable with `make aterm-mcp` or `make aterm-mcp-http`.

### Frontend — 12 Nav Tabs

| Tab | Module | Integration | Status |
|---|---|---|---|
| Project Control | Kernel-native | Direct DOM | **Live** |
| Closure Board | Kernel-native | Direct DOM + `/api/closure/*` | **Live** |
| Terminal Console | Kernel-native | WebSocket PTY, xterm.js | **Live** |
| Dual Console | Kernel-native | Two-pane terminal | **Live** |
| Workspace | Kernel-native | File browser | **Live** |
| Workspace Editor | `modules/workspace-editor/` (MWIDE copy) | Dynamic `<script>` injection | **Live** |
| System Health | Kernel-native | Direct DOM | **Live** |
| System Map | `modules/system-map/` | Shadow DOM | **Live** |
| Agent Execution | `modules/agent-execution/` (ATerm copy) | Dynamic `<script>` injection | **Live** |
| Dev Launcher | `static/dev-launcher/` | Iframe (self-contained Vite SPA) | **Live** |
| Spend Watch | Self-contained | Iframe | **Live** |
| Mac Cleanup | Self-contained | Iframe | **Live** |

### Agent Browser Runtime

| Runtime | Module | Integration | Status |
|---|---|---|---|
| Floyd Vision | `modules/agent-browser/` (Floyd Vision copy) | Default external browser runtime + Kernel Command Deck | **Live module / launch target** |

Floyd Vision is deliberately separate from the 11 in-app nav tabs. It is the browser runtime that launches the Kernel, hosts agent tabs, exposes CDP, and provides the browser-native command deck for agentic inspection.

### Theme System

- Tokyo Night-inspired dark theme
- CSS custom properties with runtime switching
- Branded title color cycling via keyframes
- WCAG AA contrast compliance on all themed surfaces

### Responsive Layout

- Three breakpoints: mobile, tablet, desktop
- Sidebar collapse on narrow viewports
- Full keyboard navigation support

### Test Coverage

- **201 collected tests** across the suite; Playwright-gated UI/proof files require the `playwright` Python package
- WebSocket behavioral tests (PTY, collab, filtered ATerm events)
- ATerm PTY integration lifecycle (create → start → run → read → stop → delete)
- Full ATerm API tests for history, search, broadcast, checkpoint, recording, verification, batch, automation, and event subscription
- MWIDE API tests (filesystem, vault, LLM proxy, git proxy)
- Governance tests (project scanning, quarantine, tags, links)
- Workflow UI tests via Playwright (requires `playwright install`)

---

## Dormant / Latent Features

These are capabilities where the infrastructure exists in the codebase but is either intentionally status-only, unwired to a frontend consumer, or still needs integration proof.

### 1. ATerm Bridge (legacy Open Anvil path)
- **What:** The `bridge` action returns explicit legacy bridge status. It is not the primary browser-control path.
- **Current direction:** Floyd Vision is the Kernel-owned default agent browser module at `modules/agent-browser/source/` and is launched with `make agent-browser`.
- **Blocker:** ATerm's Open Anvil bridge and Floyd Vision's browser MCP/CDP server use different protocols. Do not paper over that mismatch with a shim; add a Floyd Vision client if ATerm needs direct browser control.

### 2. Collab WebSocket — No UI Consumer
- **What:** The `/ws/collab` endpoint is fully functional (room-based fan-out). The Workspace Editor frontend has collab infrastructure (`collab.ts`).
- **Infrastructure:** Backend is live. Frontend code exists.
- **Blocker:** No UI exposing multi-user collaboration. No session sharing flow. The pipe is built; nobody's turned on the faucet.

### 3. Workspace Editor Backend (PTY Hub)
- **What:** MWIDE's frontend is fully copied into the Kernel and renders. The backend has `/ws/pty` for terminal access.
- **Infrastructure:** MWIDE source includes a `pty-hub` service for persistent workspace terminals.
- **Blocker:** The pty-hub was not copied. `/ws/pty` provides basic PTY but not the full workspace-aware terminal management MWIDE's frontend expects.

### 4. Bootstrap / Finisher Verification
- **What:** `POST /api/projects/{name}/bootstrap` and `POST /api/projects/{name}/finisher` endpoints exist.
- **Infrastructure:** Route handlers are wired.
- **Blocker:** Not verified to dispatch actual make targets or report accurate pass/fail. Needs integration testing against real project configs.

### 5. Agent Restart Lifecycle
- **What:** `POST /api/agents/{id}/restart` endpoint exists. `agents.json` defines agent configurations.
- **Infrastructure:** Agent CRUD is functional. Process management tracks running agents.
- **Blocker:** Full lifecycle (start → monitor → auto-restart → logs) is partially implemented. No health check polling, no log aggregation.

---

## Architecture Summary

```
control-center/
├── server.py              # FastAPI backend
├── closure_control.py     # Deterministic closure state, gates, budgets, receipts
├── index.html             # Zero-build SPA shell
├── static/                # Assets, dev-launcher, hero image
├── modules/
│   ├── agent-execution/   # ATerm copy (TypeScript SPA)
│   ├── workspace-editor/  # MWIDE copy (TypeScript SPA)
│   ├── terminal-console/  # Kernel-native terminal UI
│   ├── project-control/   # Kernel-native governance
│   ├── system-health/     # Kernel-native health monitor
│   └── system-map/        # Self-contained infrastructure map
├── tests/                 # 201 collected tests; Playwright package required for UI/proof files
├── SSOT/                  # Single source of truth
├── Issues/                # Active blockers
├── FLOYD.md               # Agent governance contract
├── agents.json            # Agent definitions
└── docs/                  # Release documentation
```

---

## Module Source Manifests

| Module | Source | Status |
|---|---|---|
| Agent Execution | ATerm (`/Volumes/SanDisk1Tb/ATerm/`) | Copied and adapted |
| Workspace Editor | MWIDE (`/Volumes/SanDisk1Tb/MWIDE/`) | Copied and adapted |
| Project Control | Kernel-native | Built for Kernel |
| Terminal Console | Kernel-native | Built for Kernel |
| System Health | Kernel-native | Built for Kernel |
| System Map | Self-contained HTML | Embedded |

Original source applications remain untouched at their canonical paths. The Kernel owns its copied implementations exclusively.

---

*This document reflects the state of the Kernel as of 2026-05-11. For the authoritative architecture record, see `SSOT/control-center_SSOT.md`.*
