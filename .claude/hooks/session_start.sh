#!/usr/bin/env bash
# session_start.sh — inject top-k memories at the start of every Claude Code session.
# Disable with: ORBITS_NO_SESSION_RECALL=1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
PYTHON="${VENV_PYTHON:-python3}"

# Silently exit if venv not set up yet
if [[ ! -x "$VENV_PYTHON" ]]; then
    exit 0
fi

# Run injector; any failure is swallowed — never block the session
"$PYTHON" -m orchestration.recall_injector --session-start --k 6 2>>"$REPO_ROOT/Knowledge/ingested/hook-errors.log" || true
