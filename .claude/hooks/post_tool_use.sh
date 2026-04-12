#!/usr/bin/env bash
# post_tool_use.sh — auto-remember significant tool outputs via Gemini curator.
# Receives JSON on stdin: {"tool_name": "...", "tool_output": "..."}
# Disable with: ORBITS_NO_AUTO_REMEMBER=1
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
LOG="$REPO_ROOT/Knowledge/ingested/hook-errors.log"

if [[ ! -x "$VENV_PYTHON" ]]; then
    exit 0
fi

[[ "${ORBITS_NO_AUTO_REMEMBER:-}" == "1" ]] && exit 0

# Read JSON payload from stdin
INPUT="$(cat)"
if [[ -z "$INPUT" ]]; then
    exit 0
fi

"$VENV_PYTHON" - <<'PYEOF' 2>>"$LOG" || true
import json, os, sys, datetime
sys.path.insert(0, os.environ.get("REPO_ROOT", "."))

input_data = json.loads(os.environ.get("HOOK_INPUT", "{}"))
tool_name   = input_data.get("tool_name", "Unknown")
tool_output = input_data.get("tool_output", "")

from orchestration.brain.curator import should_remember
import orchestration.memory as memory

ok, metadata = should_remember(tool_name, tool_output)
if not ok:
    sys.exit(0)

memory.remember(tool_output, metadata=metadata)

# Audit log
audit = {
    "ts":        datetime.datetime.utcnow().isoformat(),
    "tool":      tool_name,
    "topic":     metadata.get("topic", ""),
    "slug":      metadata.get("slug", ""),
    "tags":      metadata.get("tags", []),
    "chars":     len(tool_output),
}
log_path = os.path.join(
    os.environ.get("REPO_ROOT", "."),
    "Knowledge", "ingested",
    datetime.date.today().isoformat() + ".jsonl"
)
with open(log_path, "a") as f:
    f.write(json.dumps(audit) + "\n")
PYEOF

# Pass the input via env so the heredoc Python can read it
export REPO_ROOT
export HOOK_INPUT="$INPUT"
