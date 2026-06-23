# Agent Runtime Interface

Orbit models coding agents behind a provider-neutral runtime interface. The
interface keeps orchestration stable while allowing Codex app-server, Claude CLI,
Gemini CLI, and future providers to expose their own native semantics.

## Operations

Runtime adapters expose these operations:

- `start_session` prepares provider resources for a workspace.
- `send_turn` starts a prompt turn inside the session.
- `stream_events` emits normalized events until the active turn completes or fails.
- `stop_session` releases provider resources.
- `read_diff` returns the current workspace diff.
- `summarize_result` converts provider-specific completion data into Orbit's result shape.

Providers do not need to implement these operations with the same transport
shape. Codex can keep a live app-server thread across multiple turns. CLI
providers can represent each command invocation as a short-lived session/turn.

## Event Shape

Normalized events use this shape:

```elixir
%{
  event: :output_delta,
  timestamp: DateTime.utc_now(),
  session_id: "provider-session-or-thread-turn",
  provider: "gemini",
  harness: "cli",
  payload: %{"line" => "agent output"},
  turn_id: "optional-provider-turn-id",
  source_event: :notification,
  raw: "optional provider frame"
}
```

Required keys are `:event`, `:timestamp`, `:session_id`, `:provider`, `:harness`,
and `:payload`. `:turn_id` is optional so one-shot CLI providers are not forced
to invent Codex-style thread and turn identities.

The normalized event types are:

- `session_started`
- `turn_started`
- `output_delta`
- `tool_event`
- `diff_changed`
- `approval_needed`
- `turn_completed`
- `turn_failed`

Provider-specific protocol details stay in `payload`, `raw`, and `source_event`.
Existing Codex and CLI harness emissions keep their legacy event names and attach
the normalized event under `runtime_event` for consumers that need the new
contract.
