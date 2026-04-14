"""
agents/agent1/planner.py — Planner subagent.

Decomposes a raw user task into a structured Plan using Gemini.
Requests context from Agent 2 before planning.

Usage:
    planner = PlannerSubagent(bus)
    plan = await planner.plan("Write a web scraper for Hacker News")
"""
import asyncio
import dataclasses
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json

from orchestrator.core.bus import MessageBus, MsgType

_log = logging.getLogger("orchestrator.planner")

_CONTEXT_TIMEOUT = 10  # seconds to wait for Agent 2 context delivery


@dataclasses.dataclass
class PlanStep:
    step_id: str
    description: str
    task_type: str           # "coding" | "research" | "review" | "formatting" | etc.
    recommended_model: str
    eligible_models: list[str]
    depends_on: list[str]
    estimated_tokens: int


@dataclasses.dataclass
class Plan:
    steps: list[PlanStep]
    parallelizable: bool
    total_estimated_tokens: int
    raw: dict


class PlannerSubagent:
    AGENT_ID = "agent1_planner"

    def __init__(self, bus: MessageBus):
        self._bus = bus

    async def plan(self, task: str, context: str = "") -> Plan:
        """
        Decompose a task into a structured Plan.
        Requests context from Agent 2 if not provided.
        """
        if not context:
            context = await self._fetch_context(task)

        prompt = self._build_prompt(task, context)
        raw = await asyncio.to_thread(self._call_claude_json, prompt)

        if raw is None:
            _log.warning("Planner: Claude returned None — using fallback")
            raw = self._fallback_plan(task)

        return self._parse_plan(raw)

    def _call_claude_json(self, prompt: str) -> dict | None:
        try:
            import anthropic
            import re
            from orchestrator.core.config import ANTHROPIC_API_KEY
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return json.loads(text)
        except Exception as e:
            _log.error("Planner Claude error: %s", e)
            return None

    async def _fetch_context(self, task: str) -> str:
        """Send CONTEXT_REQUEST to Agent 2 and wait up to 10s for response."""
        msg_id = await self._bus.send(
            self.AGENT_ID, "agent2", MsgType.CONTEXT_REQUEST,
            {"topic": task}, priority=3,
        )
        deadline = asyncio.get_event_loop().time() + _CONTEXT_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            msgs = await self._bus.receive(
                self.AGENT_ID,
                msg_types=[MsgType.CONTEXT_DELIVERY],
                limit=5,
            )
            for m in msgs:
                if m.payload.get("request_id") == msg_id:
                    await self._bus.mark_read(m.id)
                    return m.payload.get("context", "")
            await asyncio.sleep(0.5)
        _log.warning("Planner: context request timed out")
        return ""

    def _build_prompt(self, task: str, context: str) -> str:
        ctx_section = f"\nRelevant context:\n{context}\n" if context else ""
        return (
            "You are a task planner. Decompose the following task into concrete steps.\n"
            f"{ctx_section}\n"
            f"Task: {task}\n\n"
            "Return a JSON object with this exact schema:\n"
            "{\n"
            '  "steps": [\n'
            "    {\n"
            '      "step_id": "step_1",\n'
            '      "description": "...",\n'
            '      "task_type": "coding|research|review|formatting|planning",\n'
            '      "recommended_model": "claude-haiku-4-5|claude-sonnet-4-6|gemini-2.5-flash",\n'
            '      "depends_on": [],\n'
            '      "estimated_tokens": 500\n'
            "    }\n"
            "  ],\n"
            '  "parallelizable": true,\n'
            '  "total_estimated_tokens": 1000\n'
            "}\n"
            "Return only the JSON object, no prose."
        )

    def _parse_plan(self, raw: dict) -> Plan:
        steps = [
            PlanStep(
                step_id=s.get("step_id", f"step_{i}"),
                description=s.get("description", ""),
                task_type=s.get("task_type", "coding"),
                recommended_model=s.get("recommended_model", "claude-haiku-4-5"),
                eligible_models=s.get("eligible_models", []),
                depends_on=s.get("depends_on", []),
                estimated_tokens=s.get("estimated_tokens", 500),
            )
            for i, s in enumerate(raw.get("steps", []))
        ]
        return Plan(
            steps=steps,
            parallelizable=raw.get("parallelizable", False),
            total_estimated_tokens=raw.get("total_estimated_tokens", sum(s.estimated_tokens for s in steps)),
            raw=raw,
        )

    def _fallback_plan(self, task: str) -> dict:
        return {
            "steps": [{"step_id": "step_1", "description": task, "task_type": "coding",
                        "recommended_model": "claude-haiku-4-5", "depends_on": [], "estimated_tokens": 1000}],
            "parallelizable": False,
            "total_estimated_tokens": 1000,
        }
