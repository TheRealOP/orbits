"""Unified CLI entrypoint for Orbits control plane."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orbits.daemon.monitor import daemon_status, load_config, start_daemon, stop_daemon


def _run(command: list[str]) -> int:
    return subprocess.run(command, cwd=REPO_ROOT).returncode


def _tmux_session_exists(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", name], cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def cmd_start(args: argparse.Namespace) -> int:
    payload: dict[str, object] = {"started": []}
    if args.monitor:
        payload["monitor"] = start_daemon(load_config())
    if args.orchestrator:
        rc = _run(["bash", "orchestrator/tmux/layout.sh"])
        payload["started"].append({"orchestrator": rc == 0, "rc": rc})
    if args.opencode:
        rc = _run(["bash", "scripts/launch_orbits.sh"])
        payload["started"].append({"opencode": rc == 0, "rc": rc})
    print(json.dumps(payload, indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    payload: dict[str, object] = {"stopped": []}
    if args.monitor:
        payload["monitor"] = stop_daemon(load_config())
    if args.orchestrator and _tmux_session_exists("orchestrator"):
        rc = subprocess.run(["tmux", "kill-session", "-t", "orchestrator"], cwd=REPO_ROOT).returncode
        payload["stopped"].append({"orchestrator": rc == 0, "rc": rc})
    if args.opencode and _tmux_session_exists("orbits_orchestrator"):
        rc = subprocess.run(["tmux", "kill-session", "-t", "orbits_orchestrator"], cwd=REPO_ROOT).returncode
        payload["stopped"].append({"opencode": rc == 0, "rc": rc})
    print(json.dumps(payload, indent=2))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    payload = {
        "monitor": daemon_status(load_config()),
        "orchestrator_tmux": _tmux_session_exists("orchestrator"),
        "opencode_tmux": _tmux_session_exists("orbits_orchestrator"),
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified Orbits control plane CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start one or more Orbits components")
    start.add_argument("--monitor", action="store_true")
    start.add_argument("--orchestrator", action="store_true")
    start.add_argument("--opencode", action="store_true")
    start.set_defaults(func=cmd_start)

    stop = subparsers.add_parser("stop", help="Stop one or more Orbits components")
    stop.add_argument("--monitor", action="store_true")
    stop.add_argument("--orchestrator", action="store_true")
    stop.add_argument("--opencode", action="store_true")
    stop.set_defaults(func=cmd_stop)

    status = subparsers.add_parser("status", help="Show Orbits component status")
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {"start", "stop"} and not (args.monitor or args.orchestrator or args.opencode):
        parser.error("Specify at least one component flag")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
