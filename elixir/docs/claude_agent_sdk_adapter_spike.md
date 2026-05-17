# Claude Agent SDK Adapter Spike

This spike validates whether Claude Agent SDK can serve as Orbit's first-class Claude runtime
adapter.

## Verdict

Claude Agent SDK is viable for an Orbit Claude adapter, provided Orbit treats it as a library-backed
agent bridge rather than a drop-in equivalent to the Codex app-server JSON-RPC protocol.

The strongest local fit is a small Node.js helper process managed by Elixir through a Port. The
helper would call the TypeScript SDK `query()` function, normalize SDK messages into Orbit's
existing agent update events, track the active `session_id`, and abort active work with an
`AbortController` when Orbit stops a run.

The SDK covers the required runtime behaviors:

- Workspace execution through the SDK `cwd` option.
- File edits through built-in `Write` and `Edit` tools.
- Command execution and command-result events through `Bash` tool uses and `tool_result` messages.
- Streaming through the SDK async iterator, with partial token/tool events enabled by
  `includePartialMessages`.
- Persistent sessions through `session_id` plus `resume`.
- Stop/cancel through `AbortController.abort()`.

## Sandbox Findings

Observed in this workspace/VM:

- Node.js `v24.15.0` and npm `11.12.1` are available.
- `npm install @anthropic-ai/claude-agent-sdk --no-audit --no-fund` succeeded.
- Installed TypeScript SDK version: `0.3.143`.
- The TypeScript package requires Node `>=18.0.0` and installed the Linux arm64 native optional
  package on this aarch64 VM.
- System Python is `3.11.2`, but `pip` and `ensurepip` are not installed.
- `uv` is installed; `uv pip install --target .tmp/claude-agent-sdk-python claude-agent-sdk`
  succeeded with Python SDK `0.2.82`.
- `claude` is installed at `/usr/bin/claude`; `claude -p` worked with existing local CLI auth.
- No `ANTHROPIC_API_KEY` or `CLAUDE_CODE_USE_*` provider environment variables were present.
- Running the SDK with an empty `CLAUDE_CONFIG_DIR` failed with
  `Not logged in - Please run /login`, so fresh workers need auth provisioning.

Official setup references:

- Agent SDK overview and install/auth guidance:
  <https://code.claude.com/docs/en/agent-sdk/overview>
- Quickstart prerequisites and package install commands:
  <https://code.claude.com/docs/en/agent-sdk/quickstart>
- Session persistence and `resume` behavior:
  <https://code.claude.com/docs/en/agent-sdk/sessions>
- Partial output streaming:
  <https://code.claude.com/docs/en/agent-sdk/streaming-output>
- Approval and user-input flow:
  <https://code.claude.com/docs/en/agent-sdk/user-input>

## Proof Result

A scratch TypeScript probe used `@anthropic-ai/claude-agent-sdk@0.3.143` with:

- `cwd` set to an isolated temporary workspace
- `allowedTools: ["Read", "Write", "Edit", "Bash", "Glob"]`
- `tools: ["Read", "Write", "Edit", "Bash", "Glob"]`
- `permissionMode: "acceptEdits"`
- `includePartialMessages: true`
- `settingSources: []`
- `model: "sonnet"`

Observed result:

- First turn created `orbit_probe.txt` and ran `ls -1 orbit_probe.txt`.
- Second turn resumed the same SDK session ID and appended to the same file.
- Final workspace file content was:

```text
first=ok
second=ok
```

- Session ID stayed stable across the resumed turns. A validated run reported
  `sameSession: true`.
- The first successful turn emitted 44 partial stream events and completed with `result:success`.
- The resumed turn emitted 40 partial stream events and completed with `result:success`.
- Assistant messages included `Write` and `Bash` tool uses.
- User/tool-result messages included command output for `pwd` and `cat orbit_probe.txt`.
- `AbortController.abort()` stopped an active SDK query with
  `Claude Code process aborted by user`.

The reusable probe lives at `elixir/spikes/claude_agent_sdk_probe`.

## Minimum Adapter Surface

### `start_session(workspace, opts)`

Recommended behavior:

- Validate that `workspace` is inside Orbit's configured workspace root before starting the SDK.
- Start or reuse a Node helper process.
- Build SDK options:
  - `cwd: workspace`
  - `model`
  - `permissionMode`
  - `allowedTools` or stricter `tools`/`disallowedTools`
  - `settingSources`
  - `includePartialMessages: true`
  - `env` containing credential/provider configuration
- Return an Orbit session struct containing:
  - helper port or process handle
  - workspace
  - provider metadata
  - current SDK `session_id` if one already exists
  - active `AbortController` token for the current turn

TypeScript's stable SDK is `query()`-centric. A session can be represented by the current
`session_id` and `cwd`, then resumed on each turn. The TypeScript SDK also exposes `startup()` for
pre-warming a Claude subprocess, but the simpler first adapter can use one `query()` call per Orbit
turn.

### `send_turn(session, prompt)`

Recommended behavior:

- Call SDK `query({ prompt, options })`.
- Set `options.resume` when `session.session_id` is known.
- Allocate a new `AbortController` per active turn.
- Stream every SDK message to the Orbit event normalizer.
- Update `session.session_id` from `system:init` or final `result`.

### `stream_events(session)`

Normalize SDK messages into Orbit update events:

- `system:init` -> `:session_started` with `session_id`, `cwd`, `model`, `permissionMode`, and tools.
- `stream_event` -> partial output/tool delta notification.
- `assistant` content blocks with `type: "tool_use"` -> tool/command-start notification.
- `user` content blocks with `type: "tool_result"` -> tool/command-result notification.
- `system` status, rate-limit, permission-denied, hook, and retry messages -> notification or error
  events.
- `result` with `is_error: false` -> `:turn_completed`.
- `result` with `is_error: true` or SDK iterator errors -> `:turn_ended_with_error`.

### `stop_session(session)`

For an active turn, call the stored `AbortController.abort()` in the Node helper. The local probe
confirmed the SDK tears down the spawned Claude Code process and raises
`Claude Code process aborted by user`.

After a completed turn, no SDK process remains if using one `query()` call per turn, so
`stop_session` is effectively a cleanup/no-op.

### `summarize_result(turn_result)`

Capture these fields from the SDK `result` message:

- `session_id`
- `subtype`
- `is_error`
- `result`
- `num_turns`
- `stop_reason`
- `total_cost_usd`
- `usage`
- `modelUsage`
- `permission_denials`

These map cleanly to Orbit's current session metrics and final turn summary.

## Implementation Notes

- Prefer TypeScript for the first adapter. It installed cleanly through npm, includes the native
  Claude Code binary as an optional dependency, and has a direct async stream API.
- Python is also usable through `uv`, but this VM lacks system `pip`; choosing Python would require
  Orbit to depend on `uv` or provision Python packaging separately.
- The SDK loads Claude Code filesystem settings by default. Use `settingSources: []` for isolated
  automation, or `["project"]` only when Orbit intentionally wants repo-provided Claude settings.
- `allowedTools` auto-approves named tools but does not restrict the tool universe by itself. For a
  stricter Orbit policy, configure `tools`, `disallowedTools`, `permissionMode`, and/or `canUseTool`.
- Sessions persist to local Claude config storage and are tied to the absolute `cwd`. Cross-host
  resume needs the same path plus persisted session files, or an SDK `sessionStore` strategy.
- The SDK does not expose Orbit's current `linear_graphql` dynamic tool protocol directly. A
  production Claude adapter should provide Linear access via MCP, SDK tools, or a prompt/workflow
  path that does not require custom tool calls.
- A fresh worker without `ANTHROPIC_API_KEY`, supported cloud-provider credentials, or a preseeded
  Claude config cannot start agent work.

## Recommendation

Proceed with a thin experimental `claude_agent_sdk` harness behind Orbit's provider abstraction.
Keep the existing Claude CLI harness as a fallback until the SDK harness proves stable under Orbit's
long-running worker lifecycle.
