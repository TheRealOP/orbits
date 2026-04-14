"""Stateless interface router for Orbits."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.core.bus import MessageBus, MsgType
from orbits.daemon.monitor import load_config

DEFAULT_STATUS = {
    "claude_sonnet": "unknown",
    "claude_haiku": "unknown",
    "gpt_5_4": "unknown",
    "interface_model": "unknown",
    "last_updated": "",
    "notes": "",
}


@dataclass(slots=True)
class RouteDecision:
    mode: str
    reason: str


def _resolve_runtime_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_model_status(config: dict | None = None) -> dict:
    config = config or load_config()
    status_path = _resolve_runtime_path(config["daemon"]["status_file"])
    if not status_path.exists():
        return dict(DEFAULT_STATUS)
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(DEFAULT_STATUS)
    return {**DEFAULT_STATUS, **data}


def decide_route(status: dict) -> RouteDecision:
    claude = status.get("claude_sonnet", "unknown")
    gpt = status.get("gpt_5_4", "unknown")

    if claude == "active" and gpt == "active":
        return RouteDecision("dual", "Claude and GPT are available.")
    if claude == "rate_limited" and gpt == "active":
        return RouteDecision("gpt_only", "Claude is rate-limited; routing to GPT path.")
    if claude == "active" and gpt in {"error", "unknown"}:
        return RouteDecision("claude_only", "GPT path unavailable; routing to Claude path.")
    return RouteDecision("queued", "No safe primary execution path is available.")


def build_user_message(status: dict, decision: RouteDecision) -> str:
    claude = status.get("claude_sonnet", "unknown")
    gpt = status.get("gpt_5_4", "unknown")
    if decision.mode == "dual":
        return "Routing normally: Claude and GPT are both available."
    if decision.mode == "gpt_only":
        return f"Claude is {claude}, so I’m routing this through the GPT fallback path."
    if decision.mode == "claude_only":
        return f"GPT is {gpt}, so I’m routing this through the Claude-only path."
    return f"Claude is {claude} and GPT is {gpt}, so I queued the task until a primary path recovers."


def append_queue_entry(task: str, mode: str, task_id: str, config: dict | None = None) -> Path:
    config = config or load_config()
    queue_path = _resolve_runtime_path(config["daemon"]["state_dir"]) / "task_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"task": task, "mode": mode, "task_id": task_id}
    with queue_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    return queue_path


async def route_task(task: str, *, task_id: str | None = None, config: dict | None = None, bus: MessageBus | None = None) -> dict:
    config = config or load_config()
    task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
    status = load_model_status(config)
    decision = decide_route(status)

    if decision.mode == "queued":
        queue_path = append_queue_entry(task, decision.mode, task_id, config)
        return {
            "task_id": task_id,
            "mode": decision.mode,
            "message": build_user_message(status, decision),
            "queue_path": str(queue_path),
        }

    owns_bus = bus is None
    if owns_bus:
        bus = MessageBus()
        await bus.init()

    try:
        await bus.send(
            "interface_agent",
            "agent1",
            MsgType.TASK_ASSIGN,
            {"task": task, "task_id": task_id, "mode": decision.mode, "status_snapshot": status},
            priority=3,
        )
    finally:
        if owns_bus:
            await bus.close()

    return {
        "task_id": task_id,
        "mode": decision.mode,
        "message": build_user_message(status, decision),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orbits interface router")
    parser.add_argument("task", help="Task text to route")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(route_task(args.task))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
