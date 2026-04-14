# PRD — Knowledge Backup System

Date: 2026-04-14

## Mission
Implement a proper, lightweight backup system for the `Knowledge/` directory.

## Stories

### US-001 — Backup archive creation
Acceptance criteria:
- A helper creates a timestamped tar.gz archive of `Knowledge/`.
- The archive is written under ignored runtime state.
- The backup excludes the backup output directory itself.

### US-002 — Manifest and retention
Acceptance criteria:
- Each backup run appends a manifest entry with timestamp, archive path, sha256, and size.
- Old backups are pruned by retention count.
- Manifest remains valid JSON after repeated runs.

### US-003 — CLI and verification
Acceptance criteria:
- A CLI wrapper runs the backup in one command.
- Unit tests cover archive creation, exclusion behavior, manifest update, and pruning.
- A smoke run creates a real backup and manifest entry.
