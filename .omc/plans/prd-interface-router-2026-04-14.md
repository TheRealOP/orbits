# PRD — Interface Router

Date: 2026-04-14
Owner: Agent 1 with Agent 2 research support and Claude Code review support

## Mission
Implement the interface agent layer that reads `orbits/state/model_status.json` and routes incoming tasks into the double-headed orchestrator or queue.

## Stories

### US-001 — Safe status-file reader
Acceptance criteria:
- The router reads `model_status.json` from config or default state path.
- If the file is absent or invalid JSON, routing falls back to `queued`.
- The status reader returns a normalized structure usable by routing logic.

### US-002 — Routing decisions
Acceptance criteria:
- Status `claude active + gpt active` routes as `dual`.
- Status `claude rate_limited + gpt active` routes as `gpt_only`.
- Status `claude active + gpt error/unknown` routes as `claude_only`.
- All other cases route as `queued`.

### US-003 — Dispatch and queue persistence
Acceptance criteria:
- Routed tasks emit a `TASK_ASSIGN` bus message with `mode` in payload metadata.
- Queued tasks append to `orbits/state/task_queue.jsonl`.
- Router returns a user-facing status string describing what happened.

### US-004 — Verification coverage
Acceptance criteria:
- Unit tests cover the four routing branches.
- Unit tests cover missing/invalid status-file fallback.
- Unit tests cover queue-file append behavior.

## Blocking rule
Do not move to deeper orchestrator handoff behavior until interface routing works against the shared status file.
