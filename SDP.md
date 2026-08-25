# Floyd's Unified Command Kernel — Software Development Plan

## Or: Glue The Working Parts Together Before Somebody Makes Another 229-Hour Spreadsheet

**DOCUMENT TYPE:** Software Development Plan
**DATE:** 2026-05-09
**PROJECT:** Floyd's Unified Command Kernel
**STATUS:** Build plan aligned to final pivot
**READER:** Engineer implementing the first MVP slice
**POST-READ ACTION:** Build the MVP without drifting into GLM-only, CLI-only, or enterprise-platform scope

---

## 1. Build Strategy

The build is a practical integration sprint, not a cathedral.

The core thesis: most of the important parts already exist somewhere in the local ecosystem. The work is to cut the right boundaries, wire the right pieces together, and keep the first version small enough to finish before the coffee gets cold and the cats file a bug report.

### 1.1 Chosen implementation posture

Build a new public project for Floyd's Unified Command Kernel.

Use existing assets as donors:

- CHIMERA for Rust workspace shape, evidence/approval contracts, shell/tool traits, and TUI layout instincts.
- ATerm for terminal intelligence and session health concepts.
- Legacy Agents Control Center for cockpit and progressive action concepts.
- Terminal Control Center for multi-terminal operator UX.
- MWIDE for vault/local filesystem boundaries and GUI/editor ideas.
- GEMINI for MacOS for model/tool schema conversion and autonomy modes.
- llm-skeptic for lightweight findings/scanner concepts.
- PEBKAC for minimal safety guardrails.

Do not turn any donor repo into the canonical public product by accident.

### 1.2 Language and runtime

Primary implementation:

- Rust for kernel, CLI/TUI, provider/tool/policy boundaries.

Supporting implementation options:

- Local web GUI may use TypeScript if fastest.
- Existing Python control-center ideas may inform GUI/API behavior, but Python should not become the canonical kernel runtime unless a very specific reason appears.

Rust earns its keep here because:

- small binaries,
- fast startup,
- strong type boundaries,
- fewer runtime footguns,
- `ratatui`/`crossterm` ecosystem,
- good fit for a local command kernel.

---

## 2. Architecture Overview

The system has one core and multiple surfaces.

```text
Floyd's Unified Command Kernel
├── Core kernel
│   ├── session/run state
│   ├── provider registry
│   ├── router
│   ├── tool registry
│   ├── evidence ledger
│   ├── approval policy
│   └── config/secrets boundary
├── Provider adapters
│   ├── GLM / Z.AI-compatible
│   ├── OpenAI-compatible
│   ├── Anthropic-compatible seam
│   ├── OpenCode Go bridge
│   ├── ZEN-style router profile
│   └── external-command adapter
├── Tool adapters
│   ├── filesystem read/list/search
│   ├── patch propose/apply
│   ├── shell/terminal execution
│   ├── MCP stdio/SSE tools
│   └── ATerm companion or terminal-intel port
├── Surfaces
│   ├── CLI
│   ├── TUI cockpit
│   ├── GUI cockpit
│   └── MCP-facing integration surface
└── Policy / safety
    ├── approval classes
    ├── destructive command guard
    ├── secret redaction
    ├── evidence requirements
    └── skeptic findings
```

### 2.1 Rule of the architecture

The kernel owns truth. Surfaces display and request actions.

No surface should own provider state, tool execution state, or approval truth independently. That is how agent tools become haunted.

---

## 3. Module Plan

### 3.1 Workspace shape

Recommended first workspace:

```text
floyd-command-kernel/
├── Cargo.toml
├── crates/
│   ├── fck-cli/
│   ├── fck-core/
│   ├── fck-provider/
│   ├── fck-router/
│   ├── fck-tools/
│   ├── fck-shell/
│   ├── fck-terminal-intel/
│   ├── fck-mcp/
│   ├── fck-policy/
│   └── fck-tui/
├── gui/
└── tests/
```

Public package names can avoid the acronym if desired. Internal crate names may use a neutral prefix if the joke is too loud for package registries.

Safer public crate naming option:

```text
command-kernel-cli
command-kernel-core
command-kernel-provider
command-kernel-router
command-kernel-tools
command-kernel-shell
command-kernel-terminal-intel
command-kernel-mcp
command-kernel-policy
command-kernel-tui
```

### 3.2 Core crate

Responsibilities:

- run/session IDs,
- objective/request types,
- run status,
- evidence references,
- approval records,
- normalized errors,
- shared traits that must not depend on UI or provider crates.

Direct donor:

- CHIMERA core types, renamed where needed.

### 3.3 Provider crate

Responsibilities:

- provider profiles,
- request/response contracts,
- streaming abstraction,
- auth source resolution,
- GLM/OpenAI-compatible adapter,
- external-command adapter,
- Anthropic/OpenCode/ZEN seams.

First adapters:

1. OpenAI-compatible HTTP adapter.
2. GLM profile using the OpenAI-compatible adapter.
3. External command adapter.

Do not create one adapter per provider if the protocol shape is identical. Provider profile data should specialize behavior before code forks do.

### 3.4 Router crate

Responsibilities:

- select provider/model for task,
- hold named routing profiles,
- support command overrides,
- later support cost/context/performance policies.

MVP router can be simple:

```text
default profile -> provider/model
fast profile -> provider/model
cheap profile -> provider/model
deep profile -> provider/model
local profile -> provider/model or external command
```

### 3.5 Tools crate

Responsibilities:

- typed tool descriptor,
- typed tool call,
- typed tool result,
- risk classification,
- evidence production,
- file/list/search/patch tools.

Direct donor:

- CHIMERA tool trait boundaries.

### 3.6 Shell crate

Responsibilities:

- shell command execution,
- command classification,
- process status,
- stdout/stderr capture,
- cancellation,
- optional persistent session seam.

Donor concepts:

- CHIMERA shell trait.
- ATerm session behavior.
- Terminal Control Center cockpit needs.

MVP can start with non-persistent shell execution if ATerm companion is used for persistent sessions. But the interface must not prevent persistent sessions later.

### 3.7 Terminal intelligence crate

Responsibilities:

- terminal state detection,
- output distillation,
- output marks,
- scrollback evidence,
- health summaries.

Direct donor:

- ATerm `StateDetector`, `DistillMode`, marks model.

MVP states:

```text
ready
busy
waiting_for_input
error
stopped
exited
```

MVP distillation modes:

```text
raw
clean
summary
structured
delta
```

### 3.8 MCP crate

Responsibilities:

- configured MCP server registry,
- start/connect health,
- tool discovery,
- tool invocation,
- evidence capture,
- schema normalization.

Donor concepts:

- GEMINI for MacOS MCP-to-model schema conversion.
- ATerm MCP-as-adapter principle.

MVP: stdio first if fastest. SSE/HTTP later.

### 3.9 Policy crate

Responsibilities:

- approval modes,
- destructive operation detection,
- secret redaction,
- repeated failure/retry detection,
- skeptic findings.

Donor concepts:

- PEBKAC minimal guardrails.
- llm-skeptic finding/severity model.
- GEMINI autonomy modes.

MVP approval modes:

```text
ask
safe-auto
auto-accept-low-risk
yolo-explicit-only
```

Do not make `yolo` easy to enable by accident. Make chaos opt-in, because even the garage has fire extinguishers.

### 3.10 TUI crate

Responsibilities:

- cockpit layout,
- run/session display,
- provider/model status,
- tool timeline,
- terminal output pane,
- patch approval pane,
- keybindings.

Donor concepts:

- CHIMERA ratatui dashboard.
- Terminal Control Center cockpit UX.
- Legacy Control Center operator dashboard.

### 3.11 GUI surface

Responsibilities:

- non-terminal cockpit,
- active run/session state,
- provider/model display,
- tool timeline,
- patch review,
- terminal/ATerm health,
- config status.

MVP GUI can be thin. It does not need full IDE features.

Candidate approaches:

1. Local web UI served by the kernel.
2. Small desktop wrapper around local web UI.
3. Adapt a Control Center style page to talk to the kernel API.

Default: local web UI, because it is fastest and easiest to inspect.

---

## 4. MVP Slices

### Slice 0 — Workspace and naming

Goal: create the project skeleton.

Tasks:

1. Create Rust workspace.
2. Add core/provider/router/tools/policy/cli crates.
3. Add format, clippy, and test commands.
4. Add README with correct product positioning.
5. Add sample config template with no real keys.

Acceptance:

- `cargo test --workspace` passes.
- `cargo fmt --all -- --check` passes.
- `cargo clippy --workspace --all-targets -- -D warnings` passes.

### Slice 1 — Provider profiles and doctor

Goal: prove BYO provider config and truthful failure.

Tasks:

1. Define provider config schema.
2. Implement environment-variable auth source resolution.
3. Implement `doctor` command.
4. Add tests for missing config, missing env var, and valid stub config.

Acceptance:

- `doctor` reports missing provider config clearly.
- No secret values appear in output.
- Tests cover failure and success paths.

### Slice 2 — OpenAI-compatible / GLM adapter

Goal: one real model call path.

Tasks:

1. Define provider request/response contract.
2. Implement OpenAI-compatible chat/completion adapter.
3. Add GLM profile using the same adapter.
4. Support non-streaming first; streaming can follow.
5. Record provider evidence.

Acceptance:

- Unit tests validate request construction without real API keys.
- Live smoke test runs only when configured.
- Missing credentials skip with explicit reason, not fake success.

### Slice 3 — Tool registry and filesystem tools

Goal: model/tool loop has safe local context.

Tasks:

1. Define tool descriptor/call/result.
2. Add read file tool.
3. Add list files tool.
4. Add search files tool.
5. Enforce workspace boundary.
6. Record evidence for each call.

Acceptance:

- Tool tests verify allowed and denied paths.
- Evidence includes tool name, inputs, status, and output summary.
- Secrets redaction is applied before output becomes model-visible.

### Slice 4 — Patch proposal flow

Goal: patch-first workflow without silent mutation.

Tasks:

1. Define patch artifact type.
2. Parse/render unified diffs.
3. Add `patch` command that proposes patch.
4. Add approval step before apply.
5. Add apply function behind explicit approval.
6. Add tests proving no auto-apply by default.

Acceptance:

- Generated patch can be reviewed.
- Patch apply requires explicit approval flag/action.
- Failed patch application reports exact failure.

### Slice 5 — Shell and terminal evidence

Goal: run commands with policy and evidence.

Tasks:

1. Implement shell command wrapper.
2. Add risk classification.
3. Add blocked destructive command set.
4. Capture stdout/stderr/status.
5. Add terminal state detection v0.
6. Add distillation modes v0.

Acceptance:

- Safe commands run.
- Destructive commands require approval or are blocked.
- Output state is structured.
- Raw logs are not dumped into model context by default.

### Slice 6 — MCP seam

Goal: prove MCP is core.

Tasks:

1. Load MCP server config.
2. Start/connect to stdio MCP server.
3. List tools.
4. Invoke one test MCP tool.
5. Normalize result into tool evidence.

Acceptance:

- Test MCP server can be discovered.
- Tool call evidence is recorded.
- MCP failures are visible and actionable.

### Slice 7 — TUI cockpit

Goal: prove terminal cockpit surface.

Tasks:

1. Add ratatui app shell.
2. Show provider/model/status footer.
3. Show conversation/objective pane.
4. Show tool timeline.
5. Show terminal/evidence pane.
6. Show patch approval pane.

Acceptance:

- TUI launches.
- A run updates visible state.
- Patch approval can be reviewed from the UI or routed to CLI confirmation.

### Slice 8 — GUI cockpit

Goal: prove product is not CLI-only.

Tasks:

1. Add local kernel status API.
2. Add minimal web cockpit.
3. Show active session/run state.
4. Show provider/model.
5. Show tool/evidence timeline.
6. Show terminal health.
7. Show patch review state.

Acceptance:

- GUI opens locally.
- It reflects real kernel state.
- It does not own separate execution truth.

---

## 5. Two-Hour MVP Compression Plan

The user wants aggressive movement today. Here is the compressed route.

### 0:00-0:15 — Skeleton

- Create workspace.
- Add core/provider/tools/policy/cli crates.
- Add basic CI-like local commands.

### 0:15-0:35 — Config and doctor

- Provider config schema.
- Env-var auth source.
- `doctor` command.

### 0:35-1:00 — Provider call path

- OpenAI-compatible adapter.
- GLM profile.
- Stubbed tests plus optional live smoke.

### 1:00-1:20 — Tools and evidence

- File read/list/search.
- Evidence ledger.
- Workspace boundary.

### 1:20-1:40 — Patch proposal

- Diff artifact.
- Review-only default.
- Explicit apply path.

### 1:40-2:00 — Minimal cockpit proof

- CLI run output with provider/tool/evidence timeline.
- If time permits, ratatui shell showing status/timeline.
- GUI is acknowledged in architecture but can come immediately after MVP if needed.

This two-hour plan creates a functional kernel spine, not the whole product. That is the point.

---

## 6. Provider and Routing Design

### 6.1 Provider profile shape

```toml
[providers.glm]
kind = "openai-compatible"
base_url = "https://api.example.com/openai/v1"
model = "glm-example"
api_key_env = "GLM_API_KEY"
enabled = true

[providers.local]
kind = "external-command"
command = "opencode-go"
args = ["run", "--json"]
enabled = false
```

No real provider endpoint should be hardcoded into committed config unless it is public documentation and contains no secret.

### 6.2 Routing profile shape

```toml
[routes.default]
provider = "glm"

[routes.fast]
provider = "local"

[routes.deep]
provider = "anthropic"

[routes.cheap]
provider = "glm"
```

The router chooses providers. Tools should not know which model is active.

---

## 7. Evidence Model

Every run produces a structured ledger.

Minimum record types:

```text
RunStarted
ProviderRequest
ProviderResponse
ToolCall
ToolResult
ShellCommand
ShellResult
PatchProposed
ApprovalRequested
ApprovalGranted
ApprovalDenied
PatchApplied
RunFailed
RunCompleted
```

Evidence must support both:

- human display,
- model-safe summarization.

Raw evidence can exist locally. Model-visible evidence must be redacted and distilled.

---

## 8. Policy Model

### 8.1 Risk classes

```text
read_only
workspace_write
shell_safe
shell_destructive
network
secret_touching
external_tool
```

### 8.2 Approval defaults

- Read-only file tools: allow.
- Search/list: allow.
- Patch proposal: allow.
- Patch application: ask.
- Shell safe command: ask or allow depending profile.
- Destructive shell command: ask/block.
- Secret-touching operation: block unless explicit trusted path.
- Network/MCP tools: ask by default until configured.

### 8.3 Secret handling

Secrets are referred to by source, never value.

Good:

```text
api_key_env = "GLM_API_KEY"
```

Bad:

```text
api_key = "actual-secret-value"
```

Any output path that sees secrets must redact before logging/evidence/UI/model context.

---

## 9. Test Plan

### 9.1 Unit tests

Required:

- provider config parsing,
- env var resolution without leaking value,
- routing profile selection,
- tool descriptor validation,
- workspace boundary enforcement,
- patch proposal no-auto-apply default,
- destructive command classification,
- terminal state detection,
- secret redaction,
- evidence ledger serialization.

### 9.2 Integration tests

Required:

- `doctor` missing config.
- `doctor` valid stub config.
- provider adapter request construction.
- local external-command adapter using a fixture command.
- file tool chain.
- patch propose/review/apply on temp workspace.
- MCP test server discovery and tool call if MCP slice is included.

### 9.3 Live tests

Live provider smoke tests must be opt-in:

- only run when env vars exist,
- skip with explicit reason otherwise,
- never print key values,
- record provider response status and model name.

---

## 10. Verification Gates

Before claiming MVP completion:

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
kernel doctor
kernel run "summarize this project" --dry-run or stub provider
kernel patch "make a harmless change" in temp fixture workspace
```

Additional required proof:

- missing provider config fails truthfully,
- no secret values in output/evidence,
- patch is not applied by default,
- model/provider can be switched through config or route override,
- at least one non-CLI surface proof exists or is wired to live kernel state.

---

## 11. Release Packaging Plan

### 11.1 MVP release package

- source repo,
- binary build instructions,
- sample config,
- provider setup guide,
- safety model guide,
- short demo script,
- ASCII-art banner usage,
- “not affiliated with providers” disclaimer.

### 11.2 Public positioning

Lead with:

> Floyd's Unified Command Kernel is a local-first agent cockpit for models, tools, terminals, MCP servers, and patches. Bring your own models. Keep your tools. Stop renting your workflow from one provider.

Do not lead with:

- the acronym,
- GLM-only framing,
- Claude Code clone framing,
- bypass/unlimited language,
- hosted API promises.

### 11.3 Community adoption hooks

- Provider profiles are easy to add.
- MCP servers are easy to register.
- Tools are typed and inspectable.
- GUI/TUI surfaces are optional adapters.
- Forks can replace routing logic without rewriting everything.

---

## 12. Risk Register

| Risk | Impact | Mitigation |
|---|---:|---|
| Scope slides back into enterprise harness | High | Keep MVP slices vertical and small |
| GLM-only framing returns | Medium | PRD and README state model-agnostic identity first |
| CLI-only implementation ignores GUI pivot | High | Add GUI seam/status API early, even if thin |
| MCP gets deferred forever | High | Include MCP seam in MVP or immediate post-MVP slice |
| Provider secrets leak | Critical | Env-ref only, redaction tests, no raw secret logging |
| Patch tool mutates unexpectedly | Critical | Proposal-first, explicit approval, test no-auto-apply |
| Donor code drags old concepts forward | Medium | Port concepts/types intentionally; no compatibility shims |
| Branding joke overwhelms trust | Medium | Full name first, technical competence always visible |

---

## 13. Build Order Recommendation

Build in this order:

1. Core evidence/approval/run model.
2. Provider config and doctor.
3. OpenAI-compatible adapter with GLM profile.
4. Router profile seam.
5. Tool registry and file tools.
6. Patch proposal/review/apply.
7. Shell execution and terminal evidence.
8. MCP stdio seam.
9. TUI cockpit.
10. GUI cockpit.
11. ATerm companion integration or native terminal-intel port, depending fastest path.

If forced to choose between pretty UI and correct patch/evidence behavior, choose correct patch/evidence behavior. Pretty can wait. Silent mutation cannot.

---

## 14. Completion Definition

The development plan is complete when the repo has:

- a runnable local kernel,
- BYO provider config,
- at least two provider profiles or one provider plus external-command adapter,
- tool registry,
- patch proposal flow,
- approval/evidence ledger,
- CLI/TUI proof,
- GUI proof or live kernel status surface,
- MCP seam proof,
- ATerm or native terminal-intel proof,
- tests proving safety boundaries.

That is the useful v1. Not the forever-product. Not the 229-hour swamp. The thing that proves Floyd's Labs can build the command center instead of renting one.

