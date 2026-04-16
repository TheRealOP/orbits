#!/usr/bin/env python3
"""Verify repo-local .claude customization stays inside this repo/fork paths."""

from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = REPO_ROOT / ".claude"
ALLOWED_PREFIXES = [REPO_ROOT, REPO_ROOT / "vendor" / "oh-my-claudecode"]


def is_allowed(target: Path) -> bool:
    resolved = target.resolve()
    return any(resolved.is_relative_to(prefix.resolve()) for prefix in ALLOWED_PREFIXES)


def main() -> int:
    violations: list[dict[str, str]] = []
    for path in CLAUDE_DIR.rglob("*"):
        if path.is_symlink():
            target = path.resolve()
            if not is_allowed(target):
                violations.append({"path": str(path.relative_to(REPO_ROOT)), "target": str(target)})

    agents_path = REPO_ROOT / "AGENTS.md"
    if agents_path.exists() and agents_path.is_symlink():
        target = agents_path.resolve()
        if not is_allowed(target):
            violations.append({"path": "AGENTS.md", "target": str(target)})

    payload = {
        "ok": not violations,
        "checked_root": str(REPO_ROOT),
        "violations": violations,
    }
    print(json.dumps(payload, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
