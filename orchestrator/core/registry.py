"""
core/registry.py — Agent registry backed by the agent_state SQLite table.

Tracks every active agent's model, status, token usage, context fill %,
and RAM. Agents are expected to call heartbeat() every ~5 seconds.

Usage:
    registry = AgentRegistry(bus)
    await registry.register("agent1", "claude-sonnet-4-6")
    await registry.heartbeat("agent1", {"tokens_used": 1200, "context_pct": 0.12})
    agents = await registry.get_all_active()
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, UTC

from orchestrator.core.bus import MessageBus

_log = logging.getLogger("orchestrator.registry")


@dataclass
class AgentState:
    agent_id: str
    model: str
    status: str  # idle | running | waiting | error
    tokens_used: int = 0
    context_pct: float = 0.0
    ram_mb: float = 0.0
    last_heartbeat: str = ""
    metadata: dict = field(default_factory=dict)


class AgentRegistry:
    def __init__(self, bus: MessageBus):
        self._bus = bus

    @property
    def _conn(self):
        return self._bus._conn

    async def register(
        self, agent_id: str, model: str, metadata: dict | None = None
    ) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO agent_state
               (agent_id, model, status, tokens_used, context_pct, ram_mb,
                last_heartbeat, metadata)
               VALUES (?, ?, 'idle', 0, 0.0, 0.0, ?, ?)""",
            (
                agent_id,
                model,
                datetime.now(UTC).isoformat(),
                json.dumps(metadata or {}),
            ),
        )
        await self._conn.commit()
        _log.info("REGISTER agent=%s model=%s", agent_id, model)

    async def deregister(self, agent_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM agent_state WHERE agent_id = ?", (agent_id,)
        )
        await self._conn.commit()
        _log.info("DEREGISTER agent=%s", agent_id)

    async def update_status(
        self, agent_id: str, status: str, metrics: dict | None = None
    ) -> None:
        m = metrics or {}
        await self._conn.execute(
            """UPDATE agent_state
               SET status = ?,
                   tokens_used = COALESCE(?, tokens_used),
                   context_pct = COALESCE(?, context_pct),
                   ram_mb      = COALESCE(?, ram_mb),
                   last_heartbeat = ?
               WHERE agent_id = ?""",
            (
                status,
                m.get("tokens_used"),
                m.get("context_pct"),
                m.get("ram_mb"),
                datetime.now(UTC).isoformat(),
                agent_id,
            ),
        )
        await self._conn.commit()

    async def get_all_active(self) -> list[AgentState]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM agent_state ORDER BY agent_id"
        )
        return [_row_to_state(r) for r in rows]

    async def get_agent(self, agent_id: str) -> AgentState | None:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM agent_state WHERE agent_id = ?", (agent_id,)
        )
        return _row_to_state(rows[0]) if rows else None

    async def heartbeat(self, agent_id: str, metrics: dict) -> None:
        await self.update_status(agent_id, "running", metrics)


def _row_to_state(r) -> AgentState:
    return AgentState(
        agent_id=r["agent_id"],
        model=r["model"] or "",
        status=r["status"] or "idle",
        tokens_used=r["tokens_used"] or 0,
        context_pct=r["context_pct"] or 0.0,
        ram_mb=r["ram_mb"] or 0.0,
        last_heartbeat=r["last_heartbeat"] or "",
        metadata=json.loads(r["metadata"] or "{}"),
    )
