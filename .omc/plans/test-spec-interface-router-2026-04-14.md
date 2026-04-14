# Test Spec — Interface Router

Date: 2026-04-14

## Unit tests
- `dual` when Claude and GPT are active.
- `gpt_only` when Claude is rate-limited and GPT is active.
- `claude_only` when Claude is active and GPT is unavailable.
- `queued` when both are unavailable.
- `queued` when status file is missing.
- `queued` when status file contains invalid JSON.
- queue file append writes a JSONL entry.

## Smoke checks
- Execute the router with a sample task and a mock active status file.
- Confirm the bus receives `TASK_ASSIGN` payload with `mode`.
- Execute the router with both heads unavailable and confirm `task_queue.jsonl` gains an entry.
