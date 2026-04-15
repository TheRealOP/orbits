#!/usr/bin/env python3
"""Run a command, mirror its output, and persist valid JSON lines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def process_stream_line(line: str, sink) -> None:
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return
    if isinstance(payload, dict):
        sink.write(json.dumps(payload) + "\n")
        sink.flush()


def run_and_capture(output_path: Path, command: list[str]) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as sink:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            process_stream_line(line, sink)
        return process.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture OpenCode JSONL telemetry while preserving terminal output")
    parser.add_argument("output", type=Path, help="JSONL output file")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("No command provided")
    return run_and_capture(args.output, command)


if __name__ == "__main__":
    raise SystemExit(main())
