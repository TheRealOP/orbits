#!/usr/bin/env bash
# scripts/launch_orbits.sh
# Sets up a 3-pane tmux session for Orchistration and tracking.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Check if tmux is installed
if ! command -v tmux &> /dev/null; then
    echo "tmux is not installed! Please install tmux first (e.g. 'brew install tmux')."
    exit 1
fi

SESSION_NAME="orbits_orchestrator"

# Install rich dependency for the dashboard if missing
if [ -d ".venv" ]; then
    .venv/bin/pip install rich psutil &>/dev/null || true
fi

# Kill old session if it exists to refresh
tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true

# 1. Create a new tmux session in detached mode
tmux new-session -d -s "$SESSION_NAME" -n "Orbits"

mkdir -p "$REPO_ROOT/orbits/state/opencode"

# Panel 0: Agent 1 Orchestrator
tmux send-keys -t "$SESSION_NAME:Orbits.0" "export ORBITS_ZOMBIE_MODE=0" C-m
tmux send-keys -t "$SESSION_NAME:Orbits.0" "python3 scripts/opencode_jsonl_wrapper.py orbits/state/opencode/agent1.jsonl -- opencode --agent agent1 --json" C-m

# 2. Split horizontal for Agent 2 memory
tmux split-window -h -t "$SESSION_NAME:Orbits"
tmux send-keys -t "$SESSION_NAME:Orbits.1" "export ORBITS_ZOMBIE_MODE=0" C-m
tmux send-keys -t "$SESSION_NAME:Orbits.1" "python3 scripts/opencode_jsonl_wrapper.py orbits/state/opencode/agent2.jsonl -- opencode --agent agent2 --json" C-m

# 3. Split the right panel vertically for Claude Code Orchestrator
tmux split-window -v -t "$SESSION_NAME:Orbits.1"
tmux send-keys -t "$SESSION_NAME:Orbits.2" "claude --dangerously-skip-permissions" C-m

# Equalize layout panes
tmux select-layout -t "$SESSION_NAME:Orbits" main-vertical

# Attach to the session
tmux attach -t "$SESSION_NAME"
