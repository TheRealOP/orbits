"""
agents/agent1/worker_manager.py — Spawns and manages worker agents.

Workers are stateless: they receive everything they need in the TASK_ASSIGN
message payload. Agent 1 never calls workers directly — all communication
goes through the bus.

Usage:
    wm = WorkerManager(bus, registry)
    worker_id = await wm.spawn_worker("step_1", "claude-haiku-4-5", prompt, context)
    results = await wm.wait_for_completion(["step_1"], timeout=120)
"""
import asyncio
import logging
import uuid

from orchestrator.core.bus import MessageBus, MsgType
from orchestrator.core.registry import AgentRegistry

_log = logging.getLogger("orchestrator.worker_manager")


def _model_family(model: str) -> str:
    m = model.lower()
    if "claude" in m:
        return "claude"
    if "gemini" in m:
        return "gemini"
    if "gpt" in m or "openai" in m:
        return "openai"
    return "claude"


class WorkerManager:
    def __init__(self, bus: MessageBus, registry: AgentRegistry):
        self._bus = bus
        self._registry = registry
        self._workers: dict[str, asyncio.Task] = {}

    async def spawn_worker(
        self,
        step_id: str,
        model: str,
        prompt: str,
        context: str = "",
        task_type: str = "coding",
    ) -> str:
        """
        Create a worker agent and send it a TASK_ASSIGN message.
        Returns the worker_id (= step_id for traceability).
        """
        worker_id = f"worker_{step_id}_{uuid.uuid4().hex[:6]}"
        await self._registry.register(worker_id, model)

        # Import the right worker class lazily to avoid circular imports
        family = _model_family(model)
        if family == "claude":
            from orchestrator.workers.claude_worker import ClaudeWorker
            worker = ClaudeWorker(worker_id, model, self._bus, self._registry)
        elif family == "gemini":
            from orchestrator.workers.gemini_worker import GeminiWorker
            worker = GeminiWorker(worker_id, model, self._bus, self._registry)
        else:
            from orchestrator.workers.openai_worker import OpenAIWorker
            worker = OpenAIWorker(worker_id, model, self._bus, self._registry)

        # Send the task — worker is already listening on the bus
        await self._bus.send(
            "agent1", worker_id, MsgType.TASK_ASSIGN,
            {
                "step_id": step_id,
                "prompt": prompt,
                "context": context,
                "model": model,
                "task_type": task_type,
            },
            priority=3,
        )

        # Run worker as asyncio task
        task = asyncio.create_task(worker.run(), name=f"worker_{worker_id}")
        self._workers[worker_id] = task
        _log.info("spawn_worker id=%s model=%s", worker_id, model)
        return worker_id

    async def wait_for_completion(
        self,
        worker_ids: list[str],
        timeout: int = 300,
    ) -> dict[str, dict]:
        """
        Poll the bus for TASK_COMPLETE / TASK_FAILED from each worker_id.
        Returns {worker_id: {"status": "complete"|"failed", "output": str, ...}}.
        """
        results: dict[str, dict] = {}
        deadline = asyncio.get_event_loop().time() + timeout

        while len(results) < len(worker_ids):
            if asyncio.get_event_loop().time() > deadline:
                for wid in worker_ids:
                    if wid not in results:
                        results[wid] = {"status": "timeout", "output": ""}
                break

            msgs = await self._bus.receive(
                "agent1",
                msg_types=[MsgType.TASK_COMPLETE, MsgType.TASK_FAILED],
                limit=20,
            )
            for msg in msgs:
                wid = msg.payload.get("worker_id", msg.from_agent)
                if wid in worker_ids and wid not in results:
                    await self._bus.mark_processed(msg.id)
                    status = "complete" if msg.msg_type == MsgType.TASK_COMPLETE else "failed"
                    results[wid] = {"status": status, **msg.payload}
                    _log.info("worker_done id=%s status=%s", wid, status)

            await asyncio.sleep(0.5)

        return results

    async def kill_worker(self, worker_id: str) -> None:
        task = self._workers.pop(worker_id, None)
        if task and not task.done():
            task.cancel()
        await self._registry.deregister(worker_id)
        _log.info("kill_worker id=%s", worker_id)

    async def get_worker_status(self, worker_id: str) -> dict:
        state = await self._registry.get_agent(worker_id)
        if state is None:
            return {"status": "not_found"}
        return {
            "status": state.status,
            "tokens_used": state.tokens_used,
            "context_pct": state.context_pct,
            "ram_mb": state.ram_mb,
        }
