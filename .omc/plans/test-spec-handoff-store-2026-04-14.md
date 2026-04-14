# Test Spec — Handoff Store

Date: 2026-04-14

## Unit tests
- write/read plan record
- write/read handoff record
- write/read decision record
- write/read session-owner record
- set pending_handoff flag
- clear pending_handoff flag without damaging other state fields

## Smoke checks
- helper can read a just-written task handoff record
- model_status.json still parses after pending_handoff updates
