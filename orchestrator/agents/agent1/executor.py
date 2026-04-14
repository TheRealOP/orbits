"""
agents/agent1/executor.py — Agent 1 main loop.

Agent 1 is the task orchestrator. It:
  1. Waits for a user task (stdin or bus)
  2. Requests context from Agent 2
  3. Uses Planner to decompose the task
  4. Uses Prompter to generate per-step prompts
  5. Spawns workers (parallel where plan allows)
  6. Collects results, assembles output
  7. Sends TASK_COMPLETE to Agent 2 for storage

TODO: middle-man interface — wire external task input here
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
import uuid

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.core.bus import MessageBus, MsgType
from orchestrator.core.metrics import MetricsTracker
from orchestrator.core.registry import AgentRegistry
from orchestrator.agents.agent1.planner import PlannerSubagent
from orchestrator.agents.agent1.prompter import PrompterSubagent
from orchestrator.agents.agent1.worker_manager import WorkerManager
from orbits.daemon.monitor import load_config
from orbits.handoff.store import read_task_record, set_pending_handoff, write_session_owner, write_task_record

_log = logging.getLogger("orchestrator.agent1")

_HEARTBEAT_INTERVAL = 5  # seconds
_MAX_RETRIES = 1  # retry failed workers once with a different model

_FALLBACK_MODELS = {
    "claude-haiku-4-5": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-haiku-4-5",
    "gpt-4o-mini": "claude-haiku-4-5",
    "deepseek-coder": "claude-haiku-4-5",
    "gemini-2.5-flash": "claude-haiku-4-5",
}


def apply_route_mode(plan, route_mode: str, config: dict | None = None):
    config = config or load_config()
    claude_model = config["models"].get("primary_orchestrator", "claude-sonnet-4-6")
    gpt_model = config["models"].get("primary_executor", "gpt-5.4")
    if route_mode == "gpt_only":
        for step in plan.steps:
            step.recommended_model = gpt_model
    elif route_mode == "claude_only":
        for step in plan.steps:
            step.recommended_model = claude_model
    return plan


def resume_plan_from_handoff(planner, task_id: str, config: dict | None = None):
    config = config or load_config()
    plan_record = read_task_record(task_id, "plan", config)
    handoff_record = read_task_record(task_id, "handoff", config)
    if not plan_record or not handoff_record:
        return None, None

    raw_plan = plan_record.get("payload") or {}
    if not raw_plan.get("steps"):
        return None, None

    plan = planner._parse_plan(raw_plan)
    handoff_payload = handoff_record.get("payload") or {}
    completed = set(handoff_payload.get("completed_step_ids", []))
    next_step = handoff_payload.get("next_step")

    remaining_steps = [step for step in plan.steps if step.step_id not in completed]
    if next_step:
        for index, step in enumerate(remaining_steps):
            if step.step_id == next_step:
                remaining_steps = remaining_steps[index:]
                break

    plan.steps = remaining_steps
    plan.parallelizable = False
    return plan, handoff_payload


class Agent1:
    AGENT_ID = "agent1"

    def __init__(self, bus: MessageBus, registry: AgentRegistry):
        self._bus = bus
        self._registry = registry
        self._metrics = MetricsTracker()
        self._planner = PlannerSubagent(bus)
        self._prompter = PrompterSubagent(bus)
        self._worker_mgr = WorkerManager(bus, registry)
        self._tokens_used = 0
        self._task_lock = asyncio.Lock()
        self._config = load_config()

    async def run(self) -> None:
        await self._registry.register(self.AGENT_ID, "claude-sonnet-4-6")
        _log.info("Agent1 started — waiting for tasks")

        asyncio.create_task(self._heartbeat_loop(), name="agent1_heartbeat")
        asyncio.create_task(self._routed_task_loop(), name="agent1_routed_tasks")

        # TODO: middle-man interface — replace stdin loop with bus/API input
        print("Agent1 ready. Type a task and press Enter (Ctrl-C to quit):")
        loop = asyncio.get_event_loop()
        while True:
            try:
                raw_task = await loop.run_in_executor(None, input, "> ")
                if raw_task.strip():
                    await self.handle_task(raw_task.strip())
            except (EOFError, KeyboardInterrupt):
                _log.info("Agent1 shutting down")
                break

    async def handle_task(self, raw_task: str) -> str:
        return await self._handle_task(raw_task, route_mode="dual")

    async def _handle_task(self, raw_task: str, route_mode: str = "dual", task_id: str | None = None) -> str:
        """
        Full task execution pipeline.
        Returns the assembled output string.
        """
        async with self._task_lock:
            task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
            _log.info("handle_task mode=%s task=%s", route_mode, raw_task[:80])
            await self._registry.update_status(self.AGENT_ID, "running")

            # 1. Request context from Agent 2
            context = await self._request_context(raw_task)

            # 2. Plan
            resumed_from_handoff = False
            handoff_payload = None
            if route_mode == "gpt_only":
                plan, handoff_payload = resume_plan_from_handoff(self._planner, task_id, self._config)
                resumed_from_handoff = plan is not None
            if not resumed_from_handoff:
                plan = await self._planner.plan(raw_task, context=context)
                plan = apply_route_mode(plan, route_mode, self._config)
            _log.info("plan: %d steps, parallelizable=%s mode=%s", len(plan.steps), plan.parallelizable, route_mode)
            plan_payload = plan.raw if hasattr(plan, "raw") else {
                "steps": [
                    {
                        "step_id": step.step_id,
                        "description": step.description,
                        "task_type": step.task_type,
                        "recommended_model": step.recommended_model,
                        "eligible_models": getattr(step, "eligible_models", []),
                        "depends_on": getattr(step, "depends_on", []),
                        "estimated_tokens": getattr(step, "estimated_tokens", 500),
                    }
                    for step in plan.steps
                ],
                "parallelizable": plan.parallelizable,
            }
            write_task_record(task_id, "plan", plan_payload, self._config)
            write_session_owner("gpt" if route_mode == "gpt_only" else "claude", task_id, self._config)
            set_pending_handoff(route_mode == "gpt_only", self._config)

            # 3. Get model recommendations from Agent 2
            if route_mode == "dual":
                for step in plan.steps:
                    packet = await self._request_model_packet(step.task_type)
                    if packet and packet.get("model"):
                        step.recommended_model = packet["model"]
                    if packet:
                        context_packet = packet.get("context_packet", {})
                        step.eligible_models = [model["id"] for model in context_packet.get("eligible_models", [])]

            # 4. Generate prompts
            prompts = await self._prompter.generate_prompts(plan)
            if resumed_from_handoff and handoff_payload:
                resume_note = handoff_payload.get("notes", "")
                next_step = handoff_payload.get("next_step", "")
                for step in plan.steps:
                    prompts[step.step_id] = (
                        f"Resume this task from stored handoff state. next_step={next_step}. "
                        f"Do not repeat completed steps. Handoff notes: {resume_note}\n\n{prompts[step.step_id]}"
                    )

            # 5. Spawn workers
            if plan.parallelizable:
                worker_ids = await asyncio.gather(*[
                    self._worker_mgr.spawn_worker(
                        step.step_id, step.recommended_model,
                        prompts[step.step_id], context, step.task_type,
                    )
                    for step in plan.steps
                ])
            else:
                worker_ids = []
                for step in plan.steps:
                    wid = await self._worker_mgr.spawn_worker(
                        step.step_id, step.recommended_model,
                        prompts[step.step_id], context, step.task_type,
                    )
                    worker_ids.append(wid)

            # 6. Wait for results, retry failures once
            results = await self._worker_mgr.wait_for_completion(list(worker_ids))
            results = await self._retry_failures(results, plan, prompts, context)

            # 7. Assemble output
            output = self._assemble_output(plan, results)
            print(f"\n{'='*60}\n{output}\n{'='*60}\n")

            # 8. Send TASK_COMPLETE to Agent 2 for storage
            await self._bus.send(
                self.AGENT_ID, "agent2", MsgType.TASK_COMPLETE,
                {
                    "task_id": task_id,
                    "task": raw_task,
                    "output": output,
                    "task_type": plan.steps[0].task_type if plan.steps else "unknown",
                    "model": "multi-worker",
                    "mode": route_mode,
                },
            )

            if route_mode == "gpt_only":
                write_task_record(
                    task_id,
                    "handoff",
                    {
                        "from": "gpt",
                        "to": "claude",
                        "completed_step_ids": [step.step_id for step in plan.steps],
                        "next_step": "",
                        "open_decisions": [],
                        "files": [],
                        "notes": "GPT resumed from stored handoff and completed the remaining steps.",
                    },
                    self._config,
                )
                set_pending_handoff(False, self._config)

            await self._registry.update_status(self.AGENT_ID, "idle")
            return output

    async def _routed_task_loop(self) -> None:
        while True:
            msgs = await self._bus.receive(self.AGENT_ID, msg_types=[MsgType.TASK_ASSIGN], limit=5)
            for msg in msgs:
                await self._bus.mark_read(msg.id)
                task = msg.payload.get("task", "")
                mode = msg.payload.get("mode", "dual")
                task_id = msg.payload.get("task_id")
                if task:
                    await self._handle_task(task, route_mode=mode, task_id=task_id)
            await asyncio.sleep(0.5)

    async def _request_context(self, task: str) -> str:
        msg_id = await self._bus.send(
            self.AGENT_ID, "agent2", MsgType.CONTEXT_REQUEST,
            {"topic": task}, priority=2,
        )
        for _ in range(20):  # 10s timeout
            msgs = await self._bus.receive(
                self.AGENT_ID, msg_types=[MsgType.CONTEXT_DELIVERY], limit=5
            )
            for m in msgs:
                if m.payload.get("request_id") == msg_id:
                    await self._bus.mark_read(m.id)
                    return m.payload.get("context", "")
            await asyncio.sleep(0.5)
        return ""

    async def _request_model_packet(self, task_type: str) -> dict | None:
        msg_id = await self._bus.send(
            self.AGENT_ID, "agent2", MsgType.MODEL_RECOMMENDATION,
            {"task_type": task_type}, priority=4,
        )
        for _ in range(6):  # 3s timeout
            msgs = await self._bus.receive(
                self.AGENT_ID, msg_types=[MsgType.MODEL_RECOMMENDATION], limit=5
            )
            for m in msgs:
                if m.payload.get("request_id") == msg_id:
                    await self._bus.mark_read(m.id)
                    return m.payload
            await asyncio.sleep(0.5)
        return None

    async def _retry_failures(self, results: dict, plan, prompts: dict, context: str) -> dict:
        for worker_id, result in list(results.items()):
            if result["status"] != "failed":
                continue
            step_id = result.get("step_id", "")
            step = next((s for s in plan.steps if s.step_id == step_id), None)
            if step is None:
                continue
            fallback = _FALLBACK_MODELS.get(step.recommended_model, "claude-haiku-4-5")
            _log.warning("retry step=%s with model=%s", step_id, fallback)
            new_wid = await self._worker_mgr.spawn_worker(
                step_id, fallback, prompts.get(step_id, step.description), context, step.task_type
            )
            retry_results = await self._worker_mgr.wait_for_completion([new_wid], timeout=120)
            results[worker_id] = retry_results.get(new_wid, {"status": "failed", "output": "Retry failed"})
        return results

    def _assemble_output(self, plan, results: dict) -> str:
        parts = []
        for step in plan.steps:
            matching = [r for r in results.values() if r.get("step_id") == step.step_id]
            output = matching[0].get("output", "") if matching else ""
            parts.append(f"## {step.description}\n{output}")
        return "\n\n".join(parts) if parts else "No output produced."

    async def _heartbeat_loop(self) -> None:
        while True:
            metrics = await self._metrics.report_metrics(
                self.AGENT_ID, self._tokens_used, "claude-sonnet-4-6"
            )
            await self._bus.send(
                self.AGENT_ID, "agent2", MsgType.HEARTBEAT, metrics, priority=8
            )
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
