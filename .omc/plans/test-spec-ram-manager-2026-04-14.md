# Test Spec — RAM Management System

Date: 2026-04-14
Scope: Mandatory RAM-management gate for the current session

## Unit tests
- Verify `safe` when total RAM is below 8 GB.
- Verify `warning` when total RAM is above 8 GB and below 10 GB.
- Verify `critical` when total RAM is at or above 10 GB.
- Verify gate allows launches in `safe` state.
- Verify gate blocks non-critical launches in `warning` state.
- Verify gate blocks launches in `critical` state.
- Verify top-process ordering is descending by memory usage.
- Verify enforcement result reports unrecovered critical pressure as failure.

## CLI smoke tests
- `status` returns a valid snapshot and writes JSON state.
- `gate` returns an explicit allow/block result.
- Loop mode runs for a short bounded interval without crashing.

## Manual checks
- Confirm the state JSON includes thresholds, total used RAM, state, top processes, and timestamp.
- Confirm known heavy non-essential processes are the only ones targeted for intervention.
- Confirm the manager remains lightweight and does not require a new dependency or external service.

## Exit criteria
- Relevant unit tests pass.
- CLI smoke checks pass.
- State persistence is visible on disk.
- Team agrees the guardrail is sufficient to unblock later phases.
