# Interface Agent

Role: cheap, stateless entrypoint.

Responsibilities:
- read `orbits/state/model_status.json`
- choose one of `dual`, `gpt_only`, `claude_only`, or `queued`
- dispatch routable tasks through the shared SQLite bus
- queue tasks conservatively when model availability is unsafe or unknown

Rules:
- prefer safety over optimism
- if status is missing or unreadable, queue the task
- always include `mode` in routed payloads
- do not keep in-memory task state between runs
