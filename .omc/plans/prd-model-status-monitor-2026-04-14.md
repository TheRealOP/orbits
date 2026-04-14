# PRD — Model Status Monitor Daemon

Date: 2026-04-14
Owner: Agent 1 with Agent 2 research support and Claude Code planning/review support
Status: Planning complete for component 1 execution

## Mission
Implement the model-status monitor daemon and shared `model_status.json` state described in `Knowledge/progress/Agent1_implementation_notes`.

## Scope
- Create `orbits/config.json` for daemon state paths and model defaults.
- Create a lightweight monitor daemon that detects Claude, OpenCode/GPT, and interface model availability.
- Persist shared state to `orbits/state/model_status.json` with atomic writes.
- Append status-change events to `orbits/state/model_status_events.jsonl`.
- Refuse to run when the RAM manager reports critical pressure.

## Stories

### US-001 — Config and schema
Acceptance criteria:
- `orbits/config.json` exists and is valid JSON.
- The config provides daemon poll interval, state directory, status-file path, and events-log path.
- The monitor can load config and fall back to safe defaults if the file is absent or incomplete.

### US-002 — One-shot daemon cycle
Acceptance criteria:
- `orbits/daemon/monitor.py` exists and exposes a one-shot execution path.
- One cycle returns a status object with `claude_sonnet`, `claude_haiku`, `gpt_5_4`, `interface_model`, `last_updated`, and `notes`.
- The daemon checks the RAM manager before running and exits cleanly if RAM is critical.

### US-003 — Atomic shared state persistence
Acceptance criteria:
- The monitor writes `orbits/state/model_status.json` atomically.
- The JSON schema matches the implementation-note contract.
- `orbits/state/model_status_events.jsonl` appends on status change.

### US-004 — Detection behavior
Acceptance criteria:
- Claude log scanning marks `claude_sonnet` as `rate_limited` when a matching mock log is present.
- Claude detection returns `active` when no matching rate-limit signal exists.
- OpenCode detection returns `active` when an `opencode` process is present, otherwise `unknown` or `error` according to probe results.
- Interface model detection uses a lightweight Gemini probe.

### US-005 — Verification coverage
Acceptance criteria:
- Unit tests cover config loading, atomic status writes, Claude log detection, and RAM-critical exit handling.
- A one-shot smoke run completes successfully.
- Status JSON and events log are created on disk.

## Execution order
1. Create config and monitor module.
2. Add state persistence helpers inside the monitor module.
3. Add unit tests.
4. Run smoke verification.

## Blocking rule
Do not begin interface-agent or orchestrator handoff work until this daemon/state layer is verified.
