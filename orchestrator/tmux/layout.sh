#!/usr/bin/env bash
# tmux/layout.sh — Launch the orchestrator in a tmux session.
#
# Layout:
#   ┌─────────────────────┬─────────────────────┐
#   │   Agent 1           │   Agent 2           │
#   ├──────────┬──────────┼─────────────────────┤
#   │  Planner │ Prompter │   Status Monitor    │
#   ├──────────┴──────────┴─────────────────────┤
#   │   User / Input pane (main interaction)    │
#   └───────────────────────────────────────────┘
#
# Usage:
#   bash tmux/layout.sh          # start fresh session
#   bash tmux/layout.sh attach   # reattach to existing

set -euo pipefail

SESSION="orchestrator"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv/bin/activate"

# Kill existing session if present (comment out to preserve state)
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Create new detached session (window 0)
tmux new-session -d -s "$SESSION" -x 220 -y 50

# ── Window 0: Main orchestrator layout ───────────────────────────────────────

# Row 1: Agent 1 (left) | Agent 2 (right)
# Start with one pane (Agent 1 — top-left)
tmux send-keys -t "$SESSION:0" "cd '$REPO_ROOT' && source '$VENV'" Enter
tmux send-keys -t "$SESSION:0" "echo '[Pane: Agent 1]'" Enter

# Split right → Agent 2
tmux split-window -t "$SESSION:0" -h
tmux send-keys -t "$SESSION:0.1" "cd '$REPO_ROOT' && source '$VENV'" Enter
tmux send-keys -t "$SESSION:0.1" "echo '[Pane: Agent 2]'" Enter

# Row 2: Planner (bottom-left of Agent 1) | Prompter | Status Monitor
# Split Agent 1 pane vertically → Planner
tmux split-window -t "$SESSION:0.0" -v
tmux send-keys -t "$SESSION:0.2" "cd '$REPO_ROOT' && source '$VENV'" Enter
tmux send-keys -t "$SESSION:0.2" "echo '[Pane: Planner]'" Enter

# Split Planner right → Prompter
tmux split-window -t "$SESSION:0.2" -h
tmux send-keys -t "$SESSION:0.3" "cd '$REPO_ROOT' && source '$VENV'" Enter
tmux send-keys -t "$SESSION:0.3" "echo '[Pane: Prompter]'" Enter

# Split Agent 2 pane vertically → Status Monitor
tmux split-window -t "$SESSION:0.1" -v
tmux send-keys -t "$SESSION:0.4" "cd '$REPO_ROOT' && source '$VENV'" Enter
tmux send-keys -t "$SESSION:0.4" "echo '[Pane: Status Monitor]'" Enter

# Row 3: Full-width User/Input pane
tmux select-pane -t "$SESSION:0.0"
tmux split-window -t "$SESSION:0" -v -p 25
tmux send-keys -t "$SESSION:0.5" "cd '$REPO_ROOT' && source '$VENV'" Enter
tmux send-keys -t "$SESSION:0.5" "echo '[Pane: User Input — run: python orchestrator/main.py]'" Enter

# ── Start services ────────────────────────────────────────────────────────────
# Uncomment these to auto-start. Comment out to start manually.

# Agent 2 (background, non-interactive)
# tmux send-keys -t "$SESSION:0.1" "PYTHONPATH='$REPO_ROOT' .venv/bin/python -c 'import asyncio; from orchestrator.agents.agent2.knowledge import Agent2; from orchestrator.core.bus import MessageBus; from orchestrator.core.registry import AgentRegistry; ...' " Enter

# Status Monitor
# tmux send-keys -t "$SESSION:0.4" "PYTHONPATH='$REPO_ROOT' .venv/bin/python -m orchestrator.core.monitor" Enter

# Full orchestrator (Agent 1 + Agent 2 + Monitor)
tmux send-keys -t "$SESSION:0.5" "PYTHONPATH='$REPO_ROOT' .venv/bin/python orchestrator/main.py" Enter

# ── Select User pane as active ────────────────────────────────────────────────
tmux select-pane -t "$SESSION:0.5"

echo "Session '$SESSION' created."
echo "Attach with: tmux attach -t $SESSION"
echo "Or run:      bash tmux/attach.sh"

# Auto-attach if running interactively
if [ -t 1 ]; then
    tmux attach -t "$SESSION"
fi
