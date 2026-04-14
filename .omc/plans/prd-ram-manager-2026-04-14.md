# PRD — RAM Management System

Date: 2026-04-14
Owner: Agent 1 (coordination), with Agent 2 research support and Claude Code design/review support
Status: Planning complete for RAM-system-only execution

## Mission
Implement a lightweight always-running RAM management system before any other repo work proceeds.

## Scope
The system must:
- monitor total RAM usage in real time
- identify major contributing processes
- enforce a warning state above 8 GB
- enforce a critical state at or above 10 GB
- block unsafe new process launches
- provide a simple callable interface for all agents
- persist the latest state for cross-agent handoff

## Reuse constraints
- Reuse existing `psutil`-based logic from `scripts/token_tracker.py`
- Reuse the current watchdog intent from `scripts/ram_watchdog.sh`
- Reuse `orchestrator/core/registry.py` and `orchestrator/core/bus.py` patterns where helpful
- No new dependencies
- Keep diffs small and reversible

## Stories

### US-001 — RAM policy core
As Agent 1,
I need a shared RAM policy module,
so every agent can classify pressure and decide whether launching more work is safe.

Acceptance criteria:
- A Python module exists for RAM-state collection and policy evaluation.
- The module returns one of `safe`, `warning`, or `critical` based on total used RAM.
- The module uses 8 GB as the soft threshold and 10 GB as the hard threshold.
- The module reports top contributing processes in descending memory order.
- The module provides a callable gate API that returns allow/block plus reason.

### US-002 — Enforcement behavior
As the orchestration team,
we need enforceable behavior when RAM is unsafe,
so execution slows or stops before the machine becomes unstable.

Acceptance criteria:
- In warning state, the system recommends or triggers reduced load behavior and blocks non-critical launches.
- In critical state, the system identifies non-essential heavy processes eligible for intervention.
- The enforcement path can terminate or otherwise stop known non-essential heavy processes safely enough for this repo's workflows.
- If pressure remains critical after enforcement attempts, the system reports failure instead of pretending recovery succeeded.

### US-003 — Lightweight interface + state persistence
As Agent 1, Agent 2, and Claude Code,
we need a simple interface and shared state file,
so all peers can check RAM safety without duplicating logic.

Acceptance criteria:
- A CLI exists for at least `status` and `gate` operations.
- The latest RAM state is written to a JSON file under repo-controlled state.
- The JSON state includes total RAM used, state, thresholds, top processes, and last-updated timestamp.
- The interface is lightweight enough to run repeatedly during execution.

### US-004 — Verification coverage
As the team,
we need proof the RAM manager behaves correctly,
so no broader implementation starts on an unverified guardrail.

Acceptance criteria:
- Unit tests cover state classification and gate behavior.
- Tests cover warning and critical thresholds.
- Tests cover the failure path where enforcement does not reduce RAM to a safe level.
- A one-shot CLI smoke check succeeds.

## Execution order
1. Implement shared RAM policy/state module.
2. Add CLI wrapper and JSON state persistence.
3. Add enforcement helpers for known heavy processes.
4. Add tests.
5. Run verification.

## Ownership
- Agent 1: implementation coordination, enforcement decisions, final verification
- Agent 2: research, state persistence, concise findings
- Claude Code: architecture review, checklist, critique, and verification support

## Blocking rule
No non-RAM-system implementation may proceed until every acceptance criterion above is verified.
