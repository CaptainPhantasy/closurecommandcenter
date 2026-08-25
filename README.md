# Floyd's Unified Command Kernel

## Or: One App To Rule The Garage

**DOCUMENT CLASSIFICATION:** README / Private Product

---

One command surface. Every Legacy AI capability wired into it. Built in a garage because the existing options annoyed us.

Floyd's Unified Command Kernel is not a shell. It's not a launcher. It's not a collection of iframes wearing a trench coat and pretending to be integrated.

It is **one application**. Terminals, workspace editing, agent execution, system health, infrastructure mapping, project governance — all copied in, all adapted, all owned by the Kernel. The originals stay standalone and untouched because that's how reuse actually works.

---

## What You're Looking At

- **Backend:** FastAPI (Python 3.14)
- **Frontend:** Zero-build vanilla JavaScript — no bundler, no npm install, no three-hour webpack config that breaks on Tuesdays
- **Agent browser:** Floyd Vision copied into `modules/agent-browser/source/`, launched with CDP and the Kernel Command Deck
- **Port:** 11527
- **Tests:** Python, dashboard, and dependency-light closure protocol suites
- **Tabs:** 12 nav surfaces covering runtime, governance, and verified closure

---

## Get It Running

```bash
make venv
make run
```

Then open `http://localhost:11527`.

For the agentic browser runtime, start the Kernel first, then launch Floyd Vision:

```bash
make agent-browser
```

`make agent-browser` opens the Kernel in the Floyd Vision browser runtime with CDP and the Kernel Command Deck extension wired for agent use. Override the browser binary with `FLOYD_VISION_BROWSER_BINARY` or the initial URL with `FLOYD_VISION_START_URL` when needed.

## Make Sure It Works

```bash
.venv/bin/python -m pytest -v
```

The full suite should pass in a complete source checkout. A few workflow tests want Playwright browsers — run `.venv/bin/playwright install` if you care about those. The deterministic closure core can also be checked without optional web dependencies:

```bash
python3 -m unittest -v tests.test_closure_control tests.test_closure_ui_contract
```

Closure writes are loopback-only by default. Remote API clients must configure a perimeter `FLOYD_CLOSURE_TOKEN` plus the role-scoped `FLOYD_CLOSURE_PRINCIPALS_JSON` registry; approval-bearing operations and explicit reconciliation of uncertain effects also require the separate `FLOYD_CLOSURE_APPROVAL_TOKEN`. The UI never embeds these values.

---

## The 12 Tabs

| Tab | What It Does |
|---|---|
| Project Control | Governance dashboard — scan projects, quarantine garbage, tag things |
| Closure Board | Ratified outcome contracts, leverage ranking, blocker truth, budgets, and receipts |
| Terminal Console | Real PTY over WebSocket, xterm.js rendering |
| Dual Console | Two terminals, because one is never enough |
| Workspace | File browser for your filesystem |
| Workspace Editor | MWIDE-based code editor, injected directly into the DOM |
| System Health | What's broken and why |
| System Map | Infrastructure cartography in a Shadow DOM |
| Agent Execution | ATerm-powered agent terminals with PTY lifecycle |
| Dev Launcher | Launch development tools (iframe, because it's a self-contained Vite SPA) |
| Spend Watch | Track where the money goes |
| Mac Cleanup | System cleanup reports |

---

## Architecture Rules We Actually Follow

1. **The Kernel is one product.** Old app names are provenance, not product labels.
2. **Source apps get copied in, then adapted.** Not rewritten from memory like a bad book report.
3. **Original source apps stay untouched.** They're standalone products.
4. **Iframes are for self-contained SPAs only.** Everything else gets direct DOM injection.
5. **All routes, ports, and names are Kernel-native.** No legacy cruft in the URL bar.

---

## The Docs

| Document | Purpose |
|---|---|
| `SSOT/control-center_SSOT.md` | The source of truth — read this before changing anything |
| `GOVERNANCE.md` | Local governance, port claim, and repo boundary summary |
| `docs/FEATURES.md` | Every feature, active and dormant, with technical detail |
| `CHANGELOG.md` | What shipped and when |
| `docs/RELEASE.md` | Release manifest |

---

## The Name

The name is intentional: this is a practical local-first command surface, not a generic dashboard wrapper.

---

┌──────────────────────────────────────────────────────────┐
│  DOCUMENT METADATA                                        │
├──────────────────────────────────────────────────────────┤
│  Classification:   README                                  │
│  Lines of Code:    11,599 Python + whole lotta TypeScript │
│  Corporate Feelings: HURT (intended)                       │
└──────────────────────────────────────────────────────────┘
