import asyncio
import contextlib
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from orchestrator.agents.agent1.executor import Agent1
from orchestrator.core.bus import MessageBus
from orchestrator.core.registry import AgentRegistry
from orbits.agents.interface.router import route_task
from orbits.handoff.store import read_task_record, write_task_record


@pytest.mark.anyio
async def test_full_claude_to_gpt_to_claude_handoff_drill():
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        config = {
            "daemon": {
                "state_dir": str(base / "state"),
                "status_file": str(base / "state" / "model_status.json"),
                "events_log": str(base / "state" / "events.jsonl"),
                "claude_log_dir": str(base / "logs"),
                "opencode_event_dir": str(base / "opencode"),
                "pid_file": str(base / "state" / "monitor.pid"),
                "poll_interval_seconds": 1,
            },
            "models": {
                "primary_orchestrator": "claude-sonnet-4-6",
                "primary_executor": "gpt-5.4",
                "interface_fallback": "gemini-2.5-flash",
            },
        }

        status_path = base / "state" / "model_status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "claude_sonnet": "rate_limited",
                    "claude_haiku": "rate_limited",
                    "gpt_5_4": "active",
                    "interface_model": "active",
                    "pending_handoff": True,
                    "last_updated": "now",
                    "notes": "",
                }
            ),
            encoding="utf-8",
        )

        task_id = "task_handoff_drill"

        # Simulate Claude planning and yielding to GPT.
        write_task_record(
            task_id,
            "plan",
            {
                "goal": "Complete the handoff drill",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "Claude planned the task",
                        "task_type": "planning",
                        "recommended_model": "claude-sonnet-4-6",
                        "eligible_models": ["claude-sonnet-4-6"],
                        "depends_on": [],
                        "estimated_tokens": 10,
                    },
                    {
                        "step_id": "step_2",
                        "description": "GPT resumes and executes remaining work",
                        "task_type": "coding",
                        "recommended_model": "gpt-5.4",
                        "eligible_models": ["gpt-5.4", "gemini-2.5-flash"],
                        "depends_on": ["step_1"],
                        "estimated_tokens": 10,
                    },
                ],
                "parallelizable": False,
                "total_estimated_tokens": 20,
            },
            config,
        )
        write_task_record(
            task_id,
            "handoff",
            {
                "from": "claude",
                "to": "gpt",
                "completed_step_ids": ["step_1"],
                "next_step": "step_2",
                "open_decisions": [],
                "files": [],
                "notes": "Claude planned the task and yielded at the rate limit.",
            },
            config,
        )

        bus = MessageBus(base / "bus.db")
        await bus.init()
        registry = AgentRegistry(bus)
        agent = Agent1(bus, registry)
        agent._config = config
        agent._request_context = AsyncMock(return_value="ctx")
        agent._planner.plan = AsyncMock(side_effect=AssertionError("planner should not rerun during GPT resume"))
        agent._prompter.generate_prompts = AsyncMock(return_value={"step_2": "finish the task"})
        agent._worker_mgr.spawn_worker = AsyncMock(return_value="worker-1")
        agent._worker_mgr.wait_for_completion = AsyncMock(
            return_value={"worker-1": {"status": "complete", "step_id": "step_2", "output": "gpt resumed remaining work"}}
        )
        agent._retry_failures = AsyncMock(
            return_value={"worker-1": {"status": "complete", "step_id": "step_2", "output": "gpt resumed remaining work"}}
        )

        consumer = asyncio.create_task(agent._routed_task_loop())
        try:
            route_result = await route_task("complete the handoff drill", task_id=task_id, config=config, bus=bus)
            await asyncio.sleep(1)
        finally:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer

        final_handoff = read_task_record(task_id, "handoff", config)
        final_status = json.loads(status_path.read_text(encoding="utf-8"))
        await bus.close()

        assert route_result["mode"] == "gpt_only"
        assert final_handoff["payload"]["from"] == "gpt"
        assert final_handoff["payload"]["to"] == "claude"
        assert final_handoff["payload"]["completed_step_ids"] == ["step_2"]
        assert final_status["pending_handoff"] is False
