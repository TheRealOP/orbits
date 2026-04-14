#!/usr/bin/env bash
# Compatibility wrapper for the session RAM manager.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="python3"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi

if [ "${1:-}" = "--loop" ]; then
    exec "$PYTHON_BIN" "$REPO_ROOT/scripts/ram_manager.py" watch --interval 5 --enforce
fi

exec "$PYTHON_BIN" "$REPO_ROOT/scripts/ram_manager.py" enforce
