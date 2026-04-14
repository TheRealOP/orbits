#!/usr/bin/env bash
# tmux/attach.sh — Reattach to an existing orchestrator tmux session.
set -euo pipefail

SESSION="orchestrator"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux attach -t "$SESSION"
else
    echo "No session named '$SESSION' found. Run tmux/layout.sh first."
    exit 1
fi
