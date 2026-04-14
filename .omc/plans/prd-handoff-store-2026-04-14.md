# PRD — Handoff Store and Pending-Handoff State

Date: 2026-04-14

## Mission
Implement the structured handoff persistence layer required for the double-headed orchestrator.

## Stories

### US-001 — Structured SLM key helpers
Acceptance criteria:
- A helper exists for writing and reading task plan, handoff, decision, and active-session records.
- Stored records use deterministic metadata for exact retrieval.
- Read helpers return the latest matching record without semantic ambiguity.

### US-002 — Pending-handoff state toggle
Acceptance criteria:
- A helper can atomically set `pending_handoff` in `orbits/state/model_status.json`.
- A helper can clear the same flag.
- Existing status fields remain intact during updates.

### US-003 — Verification coverage
Acceptance criteria:
- Unit tests cover record write/read behavior.
- Unit tests cover pending_handoff set/clear behavior.
- No executor integration begins until these helpers pass.
