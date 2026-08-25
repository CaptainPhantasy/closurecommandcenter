#!/usr/bin/env bash
# scripts/secret-scan.sh — secret-leak guardrail for TCC.
#
# Scans tracked source files for common credential patterns and exits non-zero
# if any are found. Intentionally conservative: false positives are fine, false
# negatives are not.
#
# Usage:
#   ./scripts/secret-scan.sh         # scan working tree (tracked files)
#   ./scripts/secret-scan.sh --staged  # scan only the index (pre-commit hook)
set -euo pipefail

cd "$(dirname "$0")/.."

mode="${1:-tree}"

if [ "$mode" = "--staged" ]; then
    files=$(git diff --cached --name-only --diff-filter=ACM)
else
    files=$(git ls-files)
fi

# Skip empty file list cleanly
if [ -z "$files" ]; then
    echo "secret-scan: no files to scan."
    exit 0
fi

# Patterns chosen for high signal, low FP:
# - PEM private keys
# - OpenAI/Anthropic style: sk-..., sk-ant-...
# - GitHub PAT/app: ghp_, ghs_, gho_, ghu_, ghr_
# - Slack: xoxb-, xoxp-, xoxa-, xoxr-, xoxs-
# - AWS access key id: AKIA + 16 uppercase
# - Stripe live: sk_live_..., rk_live_...
patterns='(BEGIN [A-Z ]*PRIVATE KEY|sk-(ant-)?[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]+|ghs_[A-Za-z0-9]+|gho_[A-Za-z0-9]+|ghu_[A-Za-z0-9]+|ghr_[A-Za-z0-9]+|xox[bpars]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16})'

# Skip these files (intentionally contain pattern-like strings)
skip_re='^(scripts/secret-scan\.sh|tests/artifacts/|docs/(LLM_QUICK_START|UPDATE_PLAN)\.md)$'

hits=0
while IFS= read -r f; do
    [ -z "$f" ] && continue
    [ ! -f "$f" ] && continue
    if printf '%s\n' "$f" | grep -qE "$skip_re"; then continue; fi
    if grep -InE "$patterns" "$f" 2>&1; then
        hits=$((hits + 1))
    fi
done <<< "$files"

if [ "$hits" -gt 0 ]; then
    echo ""
    echo "secret-scan: FAILED — $hits file(s) with suspected secrets."
    echo "If a hit is a false positive, add the file path to the skip_re in this script."
    exit 1
fi

echo "secret-scan: clean ($(printf '%s\n' "$files" | wc -l | tr -d ' ') files scanned)."
exit 0

