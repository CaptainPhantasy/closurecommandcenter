# Floyd's Unified Command Kernel — Product Requirements Document

## Or: The Local-First Agent Cockpit That Refuses To Join The Subscription Treadmill

**DOCUMENT TYPE:** Product Requirements Document
**DATE:** 2026-05-09
**PROJECT NAME:** Floyd's Unified Command Kernel
**WORKING FOLDER:** `/Volumes/SanDisk1Tb/Floyds_Unified_Command_Kernal`
**STATUS:** Scope-locked for first MVP build
**READER:** Builder/contributor landing cold who needs to understand what to build first
**POST-READ ACTION:** Implement or review the MVP without drifting back into the superseded GLM-only plan

---

## 1. Product Thesis

Floyd's Unified Command Kernel is a compact, local-first, model-agnostic agent command system for developers who want a Claude Code-shaped workflow without being trapped inside one model provider, one terminal surface, or one corporate hallway with beige carpet.

It is not a chat wrapper. It is not a DeepSeek-TUI clone. It is not a GLM-only harness. It is not an API resale scheme.

It is a unified working cockpit for:

- models,
- tools,
- terminals,
- patches,
- MCP servers,
- routing,
- local workflows,
- and human approval.

GLM is a first-class provider and a launch wedge because GLM has momentum and no obvious official harness. But the product thesis is bigger: one local command kernel, many model backends, many surfaces.

---

## 2. Final Settled Direction

### 2.1 Product identity

The product is **model-agnostic**.

Supported direction:

- GLM / Z.AI-compatible endpoints.
- OpenAI-compatible endpoints.
- Anthropic-compatible workflows where legally and technically appropriate.
- OpenCode Go integration.
- ZEN-style routing.
- Local or external-command model adapters.
- Future providers through a clean provider interface.

GLM remains important as:

- a strong default or showcase path,
- a launch-publicity wedge,
- a community-entry story,
- a first-class provider profile.

GLM is not the box the product lives in.

### 2.2 Market posture

This is a Claude Code competitor / alternative in the broad workflow sense:

- agentic coding loop,
- file and shell tools,
- patch-first changes,
- model-visible evidence,
- approvals,
- project context,
- terminal awareness,
- local-first execution.

It should appeal especially to builders who feel Claude Code has become weaker, narrower, more exposed, too provider-locked, or too hard to bend to their own toolchain.

The goal is not to copy Claude Code pixel-for-pixel. The goal is to build the open, adaptable, slightly feral version that works with the user's actual stack.

### 2.3 Surface posture

The product is not CLI-only.

The product has multiple surfaces sharing one core:

1. **CLI/TUI** for terminal-native users.
2. **GUI cockpit** for less terminal-comfortable users and visual monitoring.
3. **MCP integration** for advanced workflows and external tool ecosystems.
4. **ATerm integration** for smart terminal state, output health, and session awareness.
5. **Routing layer** for choosing models/providers per task.

The core must remain headless and reusable. The surfaces are adapters.

---

## 3. Product Name and Brand

### 3.1 Name

Canonical name:

```text
Floyd's Unified Command Kernel
```

Short written forms allowed after first mention:

- Unified Command Kernel
- the Command Kernel
- the Kernel

### 3.2 Acronym handling

Do not lead with the acronym.

Use the full name straight-faced. If people notice the acronym, the product should behave like it has no idea what they are talking about. The joke works because the system itself is technically credible.

### 3.3 Visual identity

The ASCII-art PNG/banner in this folder is an official brand asset candidate.

Relevant local asset:

- `ASCIIArt.md`
- `Floyds_Unified_Command_Kernal.rtfd/`

Use the ASCII-art style because it says exactly the right thing: garage-built, terminal-native, funny if you notice, useful if you do not.

### 3.4 Voice

Floyd's Labs voice should be loud, builder-first, anti-corporate, technically competent, and not sanded down into IBM wallpaper.

Allowed brand traits:

- funny,
- slightly chaotic,
- practical,
- anti-lock-in,
- open-source friendly,
- technically serious,
- straight-faced about the name.

Do not use startup sludge like “synergy,” “best-in-class,” “seamless digital transformation,” or anything that smells like a slide deck someone made to avoid writing code.

---

## 4. Target Users

### 4.1 Primary users

Builders who already use or understand agentic coding tools and want more control:

- terminal-comfortable developers,
- AI tool builders,
- open-source contributors,
- power users with multiple model accounts,
- people running local tools, MCP servers, and custom workflows,
- users who want Claude Code-like workflows without provider lock-in.

### 4.2 Secondary users

Users who want the agent cockpit but are less comfortable living entirely in the terminal:

- semi-technical builders,
- designers/product people working with codebases,
- operators monitoring multiple agent runs,
- users who benefit from a GUI control surface.

### 4.3 Non-users

The first version is not for:

- people who want bundled free model access,
- people who expect hosted SaaS accounts,
- people looking for a bypass/unlimited model loophole,
- enterprises demanding polished procurement docs on day one,
- anyone expecting the tool to be officially endorsed by Z.AI, Anthropic, OpenAI, or any other provider.

---

## 5. Product Principles

1. **Local-first.** The user's machine is the command center.
2. **Bring your own models.** The Kernel never sells or bundles API access.
3. **Model-agnostic.** GLM is first-class, not exclusive.
4. **Patch-first.** Code changes should be proposed, reviewed, and applied intentionally.
5. **Transparent tools.** The user can see what ran, why, and what evidence it produced.
6. **MCP-native.** MCP is core infrastructure, not garnish.
7. **Multi-surface.** CLI/TUI, GUI, MCP, ATerm, and routing are surfaces over one kernel.
8. **Forkable.** The project should be easy to adopt, modify, and build on.
9. **No credential weirdness.** Secrets stay local and are never echoed into prompts, logs, UI, or evidence bundles.
10. **Useful today.** The MVP target is aggressive. Avoid the 229-hour enterprise swamp.

---

## 6. MVP Definition

The MVP is a working local command kernel that proves the full product direction in a thin vertical slice.

### 6.1 MVP must include

#### Core runtime

- A local command kernel process or library that owns:
  - model provider registry,
  - tool registry,
  - session/run state,
  - evidence ledger,
  - approval policy,
  - routing decisions.

#### Provider support

At minimum:

- GLM/Z.AI-compatible provider profile.
- OpenAI-compatible provider profile.
- External-command provider adapter for local/custom routing.

Provider config must be BYO:

- endpoint,
- model,
- key/env var reference,
- optional headers,
- timeout,
- max tokens/context limits if known.

#### Routing support

MVP routing can be simple:

- default provider,
- command-level provider override,
- task-mode routing profile such as:
  - fast,
  - deep,
  - cheap,
  - local,
  - GLM showcase.

This does not need full ZEN parity on day one. It needs the seam.

#### Tool support

MVP tools:

- read file,
- list files,
- search files,
- propose patch,
- apply patch only with explicit approval,
- run shell command with policy checks,
- capture terminal output/evidence.

#### MCP support

MVP should include MCP as an advanced usage story:

- load configured MCP servers,
- list available MCP tools,
- route selected tool calls through MCP,
- record MCP tool evidence.

If full MCP client implementation threatens the same-day MVP, ship a minimal stdio JSON-RPC client first and make the interface intentionally replaceable.

#### ATerm integration

MVP should support one of these:

- direct terminal intelligence port from ATerm, or
- optional ATerm companion integration.

Required behavior:

- terminal state awareness,
- output distillation,
- command evidence,
- health/status signal visible to the user.

#### CLI/TUI surface

MVP CLI/TUI commands:

```text
kernel doctor
kernel providers
kernel run "<objective>"
kernel patch "<objective>"
kernel tui
```

The binary name can be decided during implementation. Public docs should use the project name first, binary name second.

#### GUI surface

MVP GUI can be minimal but must be acknowledged as part of the product architecture.

Acceptable first GUI:

- local web cockpit,
- desktop wrapper around local cockpit,
- or existing Control Center-inspired proof surface.

Must show:

- active run/session,
- provider/model,
- tool timeline,
- terminal/status health,
- patches/approvals.

The GUI does not need to be pretty. It needs to prove the product is not terminal-only.

---

## 7. Explicit Non-Goals

The MVP will not:

- bundle or resell model API access,
- impersonate an official provider tool,
- promise unlimited usage,
- ship a hosted SaaS backend,
- require a subscription,
- hide tool execution,
- auto-apply destructive changes without approval,
- become GLM-only,
- become CLI-only,
- spend weeks polishing enterprise docs before something runs.

---

## 8. Reuse Strategy

This is a glue-and-integration build, not a greenfield cathedral.

### 8.1 Reuse directly or heavily

- CHIMERA: Rust workspace frame, evidence/approval models, shell/tool trait boundaries, TUI layout concepts.
- ATerm: terminal state detection, output distillation, marks, health-aware terminal session design.
- llm-skeptic: lightweight finding/rule/severity model.

### 8.2 Reuse conceptually

- Legacy Agents Control Center: operator cockpit, progressive `/do` API, repo/system scan ideas.
- Terminal Control Center: multi-terminal cockpit and human/agent terminal UX.
- MWIDE: vault/local filesystem boundary, browser IDE concepts, PTY replay.
- GEMINI for MacOS: MCP/function-schema conversion, autonomy modes, model tool-call plumbing.
- PEBKAC: minimal guardrails, evidence discipline, repeat-loop detection, secrets checks.

### 8.3 Do not clone wholesale

- Full PEBKAC defense extension.
- Full MWIDE web IDE.
- Full Terminal Control Center FastAPI app.
- Full GEMINI MacOS GUI.
- Full CHIMERA creature/swarm surface.
- Full Legacy Agents Control Center browser app as the canonical v1 shell.

---

## 9. Functional Requirements

### FR1 — Provider Registry

The Kernel must load provider profiles from local config.

Each provider profile must support:

- provider kind,
- endpoint/base URL,
- model name,
- auth source by environment variable or local secret reference,
- request timeout,
- enabled/disabled state.

The Kernel must fail truthfully when config is missing.

### FR2 — Model Invocation

The Kernel must send a request to a selected provider and stream or collect the response.

The invocation record must capture:

- provider,
- model,
- prompt/request metadata,
- status,
- timing,
- token usage when available,
- error details when failed.

### FR3 — Tool Registry

The Kernel must expose a typed internal tool registry.

Each tool must declare:

- name,
- description,
- input schema,
- output schema or output contract,
- risk class,
- approval requirement,
- evidence output.

### FR4 — Patch Workflow

The Kernel must support patch proposals as a first-class artifact.

Patch flow:

1. model proposes change,
2. Kernel renders diff,
3. policy checks run,
4. user approves or rejects,
5. apply only after explicit approval,
6. evidence records action and result.

### FR5 — Terminal / Shell Execution

The Kernel must execute shell commands through a policy-aware boundary.

It must classify or block:

- destructive filesystem commands,
- git reset/restore/checkout style operations,
- commands that expose secrets,
- long-running commands that need a session.

### FR6 — Terminal Intelligence

The Kernel must expose terminal state to the user/model as structured status, not raw log soup.

States should include:

- ready,
- busy,
- waiting for input,
- error,
- stopped/exited.

Output distillation should support at least:

- raw,
- clean,
- summary,
- structured,
- delta.

### FR7 — MCP Integration

The Kernel must support MCP tools as external tool providers.

Minimum:

- configured MCP server list,
- connection health,
- tool discovery,
- tool invocation,
- evidence capture.

### FR8 — Multi-Surface Operation

The Kernel must separate core runtime from surfaces.

Required surfaces:

- CLI/TUI surface for direct usage.
- GUI/control surface for less terminal-native usage.
- MCP-facing surface for advanced integration.

### FR9 — Evidence Ledger

Every meaningful action must leave evidence:

- model calls,
- tool calls,
- shell commands,
- patch proposals,
- approvals,
- failures.

Evidence is not just logging. It is the user's receipt.

### FR10 — Secrets Handling

Secrets must never be emitted to:

- model-visible context,
- terminal output shown as evidence,
- GUI logs,
- TUI panes,
- exported reports.

---

## 10. Non-Functional Requirements

### NFR1 — Local-first install

The first install path should be local and understandable.

Preferred direction:

- Rust binary for kernel/CLI/TUI.
- Optional GUI served locally or packaged later.

### NFR2 — Fast startup

The Kernel should start fast enough to feel like a developer tool, not a Kubernetes deployment wearing a hat.

### NFR3 — Forkability

The codebase should be easy to fork:

- clear provider interface,
- clear tool interface,
- no hosted dependencies required for local use,
- documented extension points,
- no private Floyd-only assumptions in core.

### NFR4 — Safety

The MVP must default to safe behavior:

- no silent destructive commands,
- no secret leakage,
- no hidden model calls,
- no background daemon unless explicitly started.

### NFR5 — Observability

Failures must explain themselves.

The user should know:

- what failed,
- which provider/tool failed,
- what was attempted,
- whether retrying makes sense,
- what config is missing.

---

## 11. MVP Success Criteria

The MVP succeeds when a user can:

1. Configure at least one BYO provider.
2. Run `doctor` and get truthful config status.
3. Ask the Kernel to inspect a small repo or folder.
4. Let the Kernel propose a patch.
5. Review the patch before application.
6. Approve and apply the patch.
7. See model/tool/terminal evidence for the run.
8. Use either CLI/TUI or GUI surface to observe the session.
9. Connect at least one MCP server or prove the MCP seam with a local test server.
10. Switch or route between at least two provider profiles.

---

## 12. Scope Boundaries For This PRD

This PRD supersedes the older GLM-only harness framing.

Do not treat these as current requirements:

- “GLM-only users.”
- “Just a GLM harness.”
- “Just a CLI/TUI.”
- “MCP later maybe.”
- “229-hour enterprise plan.”
- “Bundled API.”
- “Direct SaaS product.”
- “Corporate-safe name.”
- “DeepSeek-TUI is the bar.”

The current target is:

> Floyd's Unified Command Kernel — a loud, compact, model-agnostic, local-first agent command system with CLI/TUI, GUI, MCP, ATerm, and routing support, using GLM as a launch wedge but not limiting the product to GLM.

---

## 13. Open Product Questions

These should not block the MVP:

1. Exact binary name.
2. Whether the first GUI is local web, desktop wrapper, or Control Center fork.
3. Which provider becomes default in sample config.
4. How aggressively to use Floyd's Labs voice in README versus marketing pages.
5. Whether the acronym is ever acknowledged in release copy.

Default answer: ship the useful kernel first, make the jokes only after it works.

