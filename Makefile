.PHONY: install venv run dev agent-browser aterm-mcp aterm-mcp-http clean help test test-api test-ui test-proof secret-scan

PYTHON ?= python3

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

venv: ## Create virtual environment and install dependencies
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	npm --prefix modules/workspace-editor/source ci
	npm --prefix modules/workspace-editor/source run build

install: ## Install Python dependencies (requires active venv — use 'make venv' first)
	pip install -r requirements.txt

run: ## Start the Kernel server on port 11527 (or PORT env var)
	python server.py

dev: ## Start with auto-reload (development)
	uvicorn server:app --host 0.0.0.0 --port 11527 --reload

agent-browser: ## Launch Kernel in Floyd Vision (kiosk + CDP + extension)
	cd modules/agent-browser/source && FLOYD_VISION_START_URL=http://127.0.0.1:11527 FLOYD_VISION_BROWSER_BINARY="$${FLOYD_VISION_BROWSER_BINARY:-/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta}" ./tools/dev/run.sh watch --manual

aterm-mcp: ## Start the ATerm MCP adapter against Kernel /api/do over stdio
	cd modules/agent-execution/source && ATERM_URL="$${ATERM_URL:-http://127.0.0.1:11527}" npm exec -- tsx src/mcp/server.ts

aterm-mcp-http: ## Start the ATerm MCP adapter over Streamable HTTP on port 9601
	cd modules/agent-execution/source && ATERM_URL="$${ATERM_URL:-http://127.0.0.1:11527}" ATERM_MCP_TRANSPORT=http ATERM_MCP_PORT="$${ATERM_MCP_PORT:-9601}" npm exec -- tsx src/mcp/server.ts

clean: ## Remove runtime artifacts
	rm -rf __pycache__ static/*.tmp .venv

test: test-api test-ui test-automation ## Run the full non-proof test suite (API + UI + automation)

test-api: ## Fast HTTP smoke tests against a running server
	.venv/bin/pytest tests/test_api_smoke.py -v

test-ui: ## Headless Playwright tests for the top workflows
	.venv/bin/pytest tests/test_workflows_ui.py -v

test-automation: ## End-to-end CRON + launchd integration tests (skips slow cron fire)
	SKIP_SLOW_CRON=1 .venv/bin/pytest tests/test_automation_e2e.py -v

test-automation-slow: ## Full automation suite including 75s real-time cron fire
	.venv/bin/pytest tests/test_automation_e2e.py -v

test-proof: ## 10/10 close→reopen→headed-floyd proof (bounces server, headed mode)
	RUN_PROOF=1 .venv/bin/pytest tests/test_proof_floyd.py -v -s

test-closure: ## Dependency-light closure protocol and UI contract tests
	$(PYTHON) -m unittest -v tests.test_closure_control tests.test_closure_ui_contract

secret-scan: ## Scan tracked files for credential patterns
	./scripts/secret-scan.sh
