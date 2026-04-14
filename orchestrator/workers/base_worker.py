"""
workers/base_worker.py — Abstract base class for all worker agents.

Workers are stateless: they receive everything in the TASK_ASSIGN payload.
Concrete subclasses implement execute() with the provider-specific SDK call.

Subclasses:
    ClaudeWorker  — Anthropic SDK
    GeminiWorker  — orchestration.gemini.ask() (CLI path)
    OpenAIWorker  — openai SDK
"""
import asyncio
import logging
import os
from abc import ABC, abstractmethod

from orchestrator.core.bus import MessageBus, MsgType
from orchestrator.core.metrics import MetricsTracker
from orchestrator.core.registry import AgentRegistry

_log = logging.getLogger("orchestrator.worker")

_POLL_INTERVAL = 0.5  # seconds between bus polls


class BaseWorker(ABC):
    def __init__(
        self,
        worker_id: str,
        model: str,
        bus: MessageBus,
        registry: AgentRegistry,
    ):
        self.worker_id = worker_id
        self.model = model
        self._bus = bus
        self._registry = registry
        self._metrics = MetricsTracker()
        self._tokens_used = 0

    async def run(self) -> None:
        """Main worker loop — poll for TASK_ASSIGN, execute, respond, exit."""
        await self._registry.register(self.worker_id, self.model)
        _log.debug("worker started id=%s model=%s", self.worker_id, self.model)

        try:
            task = await self._wait_for_task()
            if task is None:
                await self._send_failed("No task received within timeout")
                return

            prompt = task.get("prompt", "")
            context = task.get("context", "")
            step_id = task.get("step_id", self.worker_id)
            task_type = task.get("task_type", "coding")

            await self._registry.update_status(self.worker_id, "running")
            await self.report_metrics()

            output = await self.execute(prompt, context)
            self._tokens_used += self._metrics.count_tokens(output, self.model)

            await self._bus.send(
                self.worker_id, "agent1", MsgType.TASK_COMPLETE,
                {
                    "worker_id": self.worker_id,
                    "step_id": step_id,
                    "output": output,
                    "model": self.model,
                    "task_type": task_type,
                    "tokens_used": self._tokens_used,
                },
            )
            _log.info("worker done id=%s tokens=%d", self.worker_id, self._tokens_used)

        except Exception as exc:
            _log.error("worker error id=%s: %s", self.worker_id, exc)
            await self._send_failed(str(exc))
        finally:
            await self._registry.deregister(self.worker_id)

    async def _wait_for_task(self, timeout: int = 30) -> dict | None:
        """Poll bus for TASK_ASSIGN addressed to this worker."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            msgs = await self._bus.receive(
                self.worker_id, msg_types=[MsgType.TASK_ASSIGN], limit=1
            )
            if msgs:
                await self._bus.mark_read(msgs[0].id)
                return msgs[0].payload
            await asyncio.sleep(_POLL_INTERVAL)
        return None

    async def _send_failed(self, reason: str) -> None:
        await self._bus.send(
            self.worker_id, "agent1", MsgType.TASK_FAILED,
            {"worker_id": self.worker_id, "reason": reason, "model": self.model},
        )

    async def report_metrics(self) -> None:
        metrics = await self._metrics.report_metrics(
            self.worker_id, self._tokens_used, self.model
        )
        await self._registry.update_status(self.worker_id, "running", metrics)

    @abstractmethod
    async def execute(self, prompt: str, context: str) -> str:
        """Call the LLM and return the response string."""
