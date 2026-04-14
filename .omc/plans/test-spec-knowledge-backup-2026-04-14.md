# Test Spec — Knowledge Backup System

Date: 2026-04-14

## Unit tests
- backup archive is created
- archive contains Knowledge content
- backup output directory is excluded
- manifest entry is appended
- retention pruning removes oldest archive beyond limit

## Smoke checks
- CLI backup run succeeds
- backup archive exists under runtime state
- manifest file exists and contains latest backup metadata
