# Test Spec — Model Status Monitor Daemon

Date: 2026-04-14

## Unit tests
- Config loading from file and fallback defaults.
- Atomic status-file writing.
- Event-log append on status change.
- Claude rate-limit detection from a mock log directory.
- Claude active detection when no rate-limit log exists.
- RAM-critical preflight exits cleanly.

## Smoke checks
- `.venv/bin/python -m orbits.daemon.monitor --once` completes successfully.
- `orbits/state/model_status.json` exists with the required keys.
- `orbits/state/model_status_events.jsonl` contains at least one entry after the first status change.

## Manual checks
- Inject a mock rate-limit log and confirm `claude_sonnet` becomes `rate_limited`.
- Confirm interface-model probe does not hang and uses a short timeout.
- Confirm the daemon stays lightweight and does not bypass the RAM gate.
