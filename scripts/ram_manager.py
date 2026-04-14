#!/usr/bin/env python3
"""CLI wrapper for the lightweight RAM manager."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestration.ram_manager import collect_snapshot, enforce_limits, gate_launch, snapshot_to_dict, write_state


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2))


def cmd_status(_: argparse.Namespace) -> int:
    snapshot = collect_snapshot()
    write_state(snapshot)
    _print_json({"snapshot": snapshot_to_dict(snapshot)})
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    snapshot = collect_snapshot()
    write_state(snapshot)
    decision = gate_launch(essential=args.essential, snapshot=snapshot)
    _print_json({"decision": asdict(decision)})
    return 0 if decision.allowed else 2


def cmd_enforce(args: argparse.Namespace) -> int:
    result = enforce_limits(dry_run=args.dry_run)
    snapshot = collect_snapshot()
    write_state(snapshot)
    _print_json({"enforcement": asdict(result), "snapshot": snapshot_to_dict(snapshot)})
    return 0 if result.recovered or snapshot.state != "critical" else 3


def cmd_watch(args: argparse.Namespace) -> int:
    iterations = args.iterations
    count = 0
    while iterations is None or count < iterations:
        snapshot = collect_snapshot()
        write_state(snapshot)
        payload = {"snapshot": snapshot_to_dict(snapshot)}
        if args.enforce and snapshot.state == "critical":
            payload["enforcement"] = asdict(enforce_limits(dry_run=args.dry_run))
            snapshot = collect_snapshot()
            write_state(snapshot)
            payload["post_enforcement"] = snapshot_to_dict(snapshot)
        _print_json(payload)
        count += 1
        if iterations is None or count < iterations:
            time.sleep(args.interval)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lightweight RAM manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show RAM snapshot").set_defaults(func=cmd_status)

    gate_parser = subparsers.add_parser("gate", help="Check whether a new launch is allowed")
    gate_parser.add_argument("--essential", action="store_true", help="Allow only warning-state essential launches")
    gate_parser.set_defaults(func=cmd_gate)

    enforce_parser = subparsers.add_parser("enforce", help="Try to reduce critical RAM pressure")
    enforce_parser.add_argument("--dry-run", action="store_true", help="Show what would be terminated")
    enforce_parser.set_defaults(func=cmd_enforce)

    watch_parser = subparsers.add_parser("watch", help="Write repeated RAM snapshots")
    watch_parser.add_argument("--interval", type=float, default=5.0)
    watch_parser.add_argument("--iterations", type=int, default=None)
    watch_parser.add_argument("--enforce", action="store_true")
    watch_parser.add_argument("--dry-run", action="store_true")
    watch_parser.set_defaults(func=cmd_watch)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
