import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orchestrator.agents.agent1.executor import Agent1, apply_route_mode, resume_plan_from_handoff
from orbits.handoff.store import write_task_record


class _DummyBus:
    async def send(self, *args, **kwargs):
        return 1

    async def receive(self, *args, **kwargs):
        return []

    async def mark_read(self, *args, **kwargs):
        return None


class _DummyRegistry:
    async def register(self, *args, **kwargs):
        return None

    async def update_status(self, *args, **kwargs):
        return None


class TestAgent1Modes(unittest.IsolatedAsyncioTestCase):
    def test_apply_route_mode_overrides_models(self):
        plan = SimpleNamespace(steps=[SimpleNamespace(recommended_model="claude-haiku-4-5")])
        config = {"models": {"primary_orchestrator": "claude-sonnet-4-6", "primary_executor": "gpt-5.4"}}

        apply_route_mode(plan, "gpt_only", config)
        self.assertEqual(plan.steps[0].recommended_model, "gpt-5.4")

        apply_route_mode(plan, "claude_only", config)
        self.assertEqual(plan.steps[0].recommended_model, "claude-sonnet-4-6")

    async def test_handle_task_writes_handoff_state_for_gpt_only(self):
        agent = Agent1(_DummyBus(), _DummyRegistry())
        plan = SimpleNamespace(
            steps=[SimpleNamespace(step_id="step_1", description="do work", task_type="coding", recommended_model="claude-haiku-4-5")],
            parallelizable=False,
            raw={"steps": [{"step_id": "step_1"}]},
        )

        agent._request_context = AsyncMock(return_value="ctx")
        agent._planner.plan = AsyncMock(return_value=plan)
        agent._prompter.generate_prompts = AsyncMock(return_value={"step_1": "prompt"})
        agent._worker_mgr.spawn_worker = AsyncMock(return_value="worker-1")
        agent._worker_mgr.wait_for_completion = AsyncMock(return_value={"worker-1": {"status": "complete", "step_id": "step_1", "output": "done"}})
        agent._retry_failures = AsyncMock(return_value={"worker-1": {"status": "complete", "step_id": "step_1", "output": "done"}})

        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "daemon": {
                    "state_dir": str(Path(temp_dir) / "state"),
                    "status_file": str(Path(temp_dir) / "state" / "model_status.json"),
                    "events_log": str(Path(temp_dir) / "state" / "events.jsonl"),
                    "claude_log_dir": str(Path(temp_dir) / "logs"),
                    "poll_interval_seconds": 1,
                },
                "models": {"primary_orchestrator": "claude-sonnet-4-6", "primary_executor": "gpt-5.4"},
            }
            agent._config = config
            with patch("orchestrator.agents.agent1.executor.write_task_record") as write_task_record, patch(
                "orchestrator.agents.agent1.executor.write_session_owner"
            ) as write_session_owner, patch("orchestrator.agents.agent1.executor.set_pending_handoff") as set_pending_handoff:
                result = await agent._handle_task("test task", route_mode="gpt_only")

        self.assertIn("done", result)
        self.assertEqual(plan.steps[0].recommended_model, "gpt-5.4")
        self.assertEqual(write_task_record.call_count, 2)
        write_session_owner.assert_called_once()
        self.assertEqual(set_pending_handoff.call_args_list[0].args, (True, config))

    async def test_dual_mode_uses_context_packet_eligible_models(self):
        agent = Agent1(_DummyBus(), _DummyRegistry())
        plan = SimpleNamespace(
            steps=[SimpleNamespace(step_id="step_1", description="do work", task_type="coding", recommended_model="claude-haiku-4-5", eligible_models=[])],
            parallelizable=False,
            raw={"steps": [{"step_id": "step_1"}]},
        )
        agent._request_context = AsyncMock(return_value="ctx")
        agent._planner.plan = AsyncMock(return_value=plan)
        agent._request_model_packet = AsyncMock(
            return_value={
                "model": "gpt-5.4",
                "context_packet": {"eligible_models": [{"id": "gpt-5.4"}, {"id": "gemini-2.5-flash"}]},
            }
        )
        agent._prompter.generate_prompts = AsyncMock(return_value={"step_1": "prompt"})
        agent._worker_mgr.spawn_worker = AsyncMock(return_value="worker-1")
        agent._worker_mgr.wait_for_completion = AsyncMock(return_value={"worker-1": {"status": "complete", "step_id": "step_1", "output": "done"}})
        agent._retry_failures = AsyncMock(return_value={"worker-1": {"status": "complete", "step_id": "step_1", "output": "done"}})
        with patch("orchestrator.agents.agent1.executor.write_task_record"), patch(
            "orchestrator.agents.agent1.executor.write_session_owner"
        ), patch("orchestrator.agents.agent1.executor.set_pending_handoff"):
            await agent._handle_task("test task", route_mode="dual")

        self.assertEqual(plan.steps[0].recommended_model, "gpt-5.4")
        self.assertEqual(plan.steps[0].eligible_models, ["gpt-5.4", "gemini-2.5-flash"])

    def test_resume_plan_from_handoff_skips_completed_steps(self):
        planner = SimpleNamespace(
            _parse_plan=lambda raw: SimpleNamespace(
                steps=[
                    SimpleNamespace(step_id="step_1", description="one", task_type="coding", recommended_model="gpt-5.4"),
                    SimpleNamespace(step_id="step_2", description="two", task_type="coding", recommended_model="gpt-5.4"),
                    SimpleNamespace(step_id="step_3", description="three", task_type="coding", recommended_model="gpt-5.4"),
                ],
                parallelizable=True,
                raw=raw,
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "daemon": {
                    "state_dir": str(Path(temp_dir) / "state"),
                    "status_file": str(Path(temp_dir) / "state" / "model_status.json"),
                    "events_log": str(Path(temp_dir) / "state" / "events.jsonl"),
                    "claude_log_dir": str(Path(temp_dir) / "logs"),
                    "opencode_event_dir": str(Path(temp_dir) / "opencode"),
                    "poll_interval_seconds": 1,
                },
                "models": {"primary_orchestrator": "claude-sonnet-4-6", "primary_executor": "gpt-5.4"},
            }
            write_task_record("task-1", "plan", {"steps": [{"step_id": "step_1"}, {"step_id": "step_2"}, {"step_id": "step_3"}]}, config)
            write_task_record(
                "task-1",
                "handoff",
                {"completed_step_ids": ["step_1"], "next_step": "step_2", "notes": "resume here"},
                config,
            )
            plan, handoff = resume_plan_from_handoff(planner, "task-1", config)
        self.assertEqual([step.step_id for step in plan.steps], ["step_2", "step_3"])
        self.assertEqual(handoff["next_step"], "step_2")


if __name__ == "__main__":
    unittest.main()
