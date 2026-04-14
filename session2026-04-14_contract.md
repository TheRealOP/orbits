# Session 2026-04-14 Contract

## Shared mission
- Keep the system stable while completing repo work.
- No implementation beyond RAM management may begin until the RAM management system is complete and verified.

## RAM limits and enforcement
- Soft limit: 8 GB total RAM used.
- Hard cap: 10 GB total RAM used.
- Warning state at >8 GB: reduce load, avoid new non-critical processes.
- Critical state at >=10 GB: halt non-essential execution, kill/pause processes until safe.
- If RAM cannot be reduced below safe thresholds, stop execution and report failure.

## Planning-first requirement
- Read source-of-truth files before execution.
- Agree on a plan before implementation.
- No implementation begins until this contract exists, the plan is recorded, and all 3 agents have signed.

## Model routing and optimization
- Start with cheapest reliable model.
- Lightweight: `openai/gpt-5.4-mini`, `openai/gpt-5.4-mini-fast`, `google/gemini-2.5-flash`, `google/gemini-2.5-flash` for summaries, scans, formatting, and simple edits. # edited by gemini
- General: `openai/gpt-5.4-fast`, `openai/gpt-5.4`, `openai/gpt-5.3-codex-spark`, `google/gemini-2.5-flash`, `google/gemini-3-flash-preview` for standard coding and medium tasks.
- Heavy coding: `openai/gpt-5.1-codex`, `openai/gpt-5.2-codex`, `openai/gpt-5.3-codex`, `openai/gpt-5-codex`, and `openai/gpt-5.1-codex-max` only when justified.
- Deep reasoning/review: `openai/gpt-5.4`, Claude Code, `google/gemini-2.5-pro`, `google/gemini-3-pro-preview`, `google/gemini-3.1-pro-preview` only for planning, architecture, hard debugging, or strong review when justified.
- Avoid multiple heavy models in parallel unless necessary.
- Respect RAM, token, and rate-limit constraints before each step.
- Record model-to-task usage in concise logs/state summaries.

## Role definitions
- Agent 1: execution lead, RAM enforcement, coordination, quality gatekeeping.
- Agent 2: knowledge persistence, context brokering, state tracking, concise summaries.
- Claude Code peer: planning, complex reasoning, debugging, OMC-assisted workflows.

## Halt conditions
- RAM >= 10 GB.
- Required contract signatures missing.
- RAM system not yet complete and verified.
- Unsafe token/rate-limit conditions with no safe handoff.
- A required dependency for the current step is unresolved.

## Context and state preservation
- Agent 1 logs to `Knowledge/logs/agent1/`.
- Agent 2 persists shared state summaries.
- Keep logs concise; summarize often.
- Use tmux peer messaging for coordination and acknowledgements.

## Token and rate-limit handoffs
- If a provider is constrained, hand work to an available lighter/cheaper path first.
- Record handoff summaries before switching ownership.
- Avoid redundant parallel model use.

## Current-phase ownership
- Agent 1: draft contract, gather signatures, define RAM-system plan, enforce execution block.
- Agent 2: review contract, persist session state, help maintain plan/task registry.
- Claude Code peer: review contract, critique RAM-system design and verification approach.

## RAM system requirements for current phase
- Must monitor total RAM usage in real time.
- Must identify major contributing processes.
- Must enforce warning state above 8 GB and critical state at or above 10 GB.
- Must block or prevent unsafe new process launches.
- Must provide a lightweight callable interface usable by Agent 1, Agent 2, and Claude Code.
- Must remain always running during active execution.

## Initial plan (blocking phase)
1. Confirm current RAM baseline and active peer layout.
2. Design a lightweight always-on RAM manager with callable interface and enforcement states.
3. Get peer review/sign-off on contract and plan.
4. Implement RAM manager only.
5. Verify RAM manager behavior, thresholds, interface, and blocking logic.
6. Only then plan broader repo execution.

## Signature status
- Agent 1: SIGNED.
- Agent 2: SIGNED.
- Claude Code peer: SIGNED.

## Signatures
- Agent 1: confirmed agreement on 2026-04-14.
- Agent 2: "AGENT 2 SIGNED: I agree to the operating rules and initial blocking plan for session 2026-04-14."
- Claude Code peer: "CLAUDE CODE SIGNED: I agree to the operating rules and initial blocking plan for session 2026-04-14."

## Compliance gate
- Contract file exists: YES.
- All three agents signed: YES.
- Initial blocking plan recorded in this contract: YES.
- Execution status: unblocked for RAM-system design/implementation only; all other implementation remains blocked until RAM system is complete and verified.

## Amendment A — Post-RAM execution phase
- RAM management system status: COMPLETE and VERIFIED on 2026-04-14.
- New current objective: stabilize and verify the Gemini-backed memory pipeline before broader orchestration changes.
- Priority order for the next phase:
  1. Fix Gemini smoke-test/configuration drift (invalid legacy model references and CLI invocation assumptions).
  2. Re-run doctor/smoke checks with the valid Gemini CLI path.
  3. Execute the end-to-end memory smoke test from `Knowledge/progress/TODO.md` and `Knowledge/progress/implementation_plan.md`.
  4. Only after the smoke path is healthy, proceed to larger orchestrator/model-handoff implementation items.
- Updated ownership for this phase:
  - Agent 1: orchestrate fixes, verification, and RAM-safe execution.
  - Agent 2: research/config audit, persist findings, and summarize blockers.
  - Claude Code peer: critique plan, review implementation, and validate acceptance criteria.
- Amendment acknowledgement required from Agent 1, Agent 2, and Claude Code before broader post-RAM execution proceeds.

## Amendment A acknowledgements
- Agent 1: ACK — I agree with the post-RAM execution phase priorities and ownership.
- Agent 2: ACK — "AGENT 2 ACK AMENDMENT A: I agree with the post-RAM execution phase priorities and ownership."
- Claude Code peer: ACK — "CLAUDE CODE ACK AMENDMENT A: I agree with the post-RAM execution phase priorities and ownership."
