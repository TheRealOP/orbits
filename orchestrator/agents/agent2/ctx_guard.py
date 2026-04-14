"""
agents/agent2/ctx_guard.py — Context window overflow monitor.

Polls agent_state every 10 seconds. On threshold breach:
  WARN (75%):     sends CONTEXT_WARN to the agent
  COMPRESS (90%): requests history, compresses it, sends CONTEXT_COMPRESS

Usage:
    guard = ContextWindowGuard(bus, registry, distiller)
    await guard.monitor_loop()   # runs forever as a background asyncio task
"""
import asyncio
import logging

from orchestrator.core.bus import MessageBus, MsgType
from orchestrator.core.config import CONTEXT_HARD_LIMIT, CONTEXT_WARN_THRESHOLD
from orchestrator.core.registry import AgentRegistry

_log = logging.getLogger("orchestrator.ctx_guard")

_POLL_INTERVAL = 10  # seconds


class ContextWindowGuard:
    WARN_THRESHOLD = CONTEXT_WARN_THRESHOLD
    COMPRESS_THRESHOLD = CONTEXT_HARD_LIMIT

    def __init__(self, bus: MessageBus, registry: AgentRegistry, distiller=None):
        self._bus = bus
        self._registry = registry
        self._distiller = distiller
        self._warned: set[str] = set()    # agents already warned this cycle
        self._compressed: set[str] = set()  # agents already compressed this cycle

    async def monitor_loop(self) -> None:
        """Run continuously. Call as asyncio background task."""
        _log.info("ContextWindowGuard started (poll every %ds)", _POLL_INTERVAL)
        while True:
            try:
                await self._tick()
            except Exception as exc:
                _log.error("monitor_loop error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _tick(self) -> None:
        agents = await self._registry.get_all_active()
        for agent in agents:
            if agent.agent_id == "agent2":
                continue  # guard does not monitor itself
            info = await self.check_agent(agent.agent_id)
            pct = info["fill_pct"]

            if pct >= self.COMPRESS_THRESHOLD and agent.agent_id not in self._compressed:
                await self._handle_compress(agent.agent_id, pct)
                self._compressed.add(agent.agent_id)
                self._warned.add(agent.agent_id)

            elif pct >= self.WARN_THRESHOLD and agent.agent_id not in self._warned:
                await self._handle_warn(agent.agent_id, pct)
                self._warned.add(agent.agent_id)

            elif pct < self.WARN_THRESHOLD:
                # Reset once agent drops back below warn threshold
                self._warned.discard(agent.agent_id)
                self._compressed.discard(agent.agent_id)

    async def check_agent(self, agent_id: str) -> dict:
        state = await self._registry.get_agent(agent_id)
        if state is None:
            return {"fill_pct": 0.0, "tokens_used": 0, "action_needed": None}

        pct = state.context_pct
        action = None
        if pct >= self.COMPRESS_THRESHOLD:
            action = "compress"
        elif pct >= self.WARN_THRESHOLD:
            action = "warn"

        return {
            "fill_pct": pct,
            "tokens_used": state.tokens_used,
            "tokens_remaining": max(0, int((1.0 - pct) * 200_000)),  # rough estimate
            "action_needed": action,
        }

    async def _handle_warn(self, agent_id: str, pct: float) -> None:
        _log.warning("CONTEXT_WARN agent=%s fill=%.1f%%", agent_id, pct * 100)
        await self._bus.send(
            "agent2", agent_id, MsgType.CONTEXT_WARN,
            {"fill_pct": pct, "message": f"Context window is {pct*100:.0f}% full."},
            priority=2,
        )

    async def _handle_compress(self, agent_id: str, pct: float) -> None:
        _log.warning("CONTEXT_COMPRESS agent=%s fill=%.1f%%", agent_id, pct * 100)
        # Request history from agent via a CONTEXT_REQUEST message
        await self._bus.send(
            "agent2", agent_id, MsgType.CONTEXT_REQUEST,
            {"topic": "__history__", "reason": "compress", "fill_pct": pct},
            priority=1,
        )
        # Note: actual compression happens when agent responds with history.
        # The agent2 main loop handles the CONTEXT_DELIVERY response and
        # calls distiller.compress_conversation(), then sends CONTEXT_COMPRESS.
        await self._bus.send(
            "agent2", agent_id, MsgType.CONTEXT_COMPRESS,
            {"fill_pct": pct, "message": "Context window critical — please summarise and reset history."},
            priority=1,
        )
