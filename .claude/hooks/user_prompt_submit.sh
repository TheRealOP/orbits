#!/usr/bin/env bash
# user_prompt_submit.sh — inject relevant memories for each user prompt.
# Receives the prompt text on stdin (Claude Code hook contract).
# Disable with: ORBITS_NO_PROMPT_INJECT=1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    exit 0
fi

# Read prompt from stdin
PROMPT="$(cat)"

if [[ -z "$PROMPT" ]]; then
    exit 0
fi

"$VENV_PYTHON" -m orchestration.recall_injector --query "$PROMPT" --k 5 \
    2>>"$REPO_ROOT/Knowledge/ingested/hook-errors.log" || true
