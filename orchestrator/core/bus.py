"""
core/bus.py — Async SQLite message bus for inter-agent communication.

All agents communicate exclusively through this bus — no direct function calls
between agents. Every send/receive is logged to logs/bus.log.

Usage:
    bus = MessageBus()
    await bus.init()
    await bus.send("agent1", "agent2", MsgType.CONTEXT_REQUEST, {"topic": "..."})
    messages = await bus.receive("agent2", msg_types=[MsgType.CONTEXT_REQUEST])
"""
import asyncio
from datetime import UTC
import json
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel

from orchestrator.core.config import BUS_DB_PATH, LOGS_DIR

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KNOWLEDGE_LOGS = _REPO_ROOT / "Knowledge" / "logs"
for _subdir in ("agent1", "agent2", "misc_agent"):
    (_KNOWLEDGE_LOGS / _subdir).mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
_log = logging.getLogger("orchestrator.bus")
_bus_log_handler = logging.FileHandler(LOGS_DIR / "bus.log")
_bus_log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_log.addHandler(_bus_log_handler)
_log.setLevel(logging.DEBUG)


# ── Message types ─────────────────────────────────────────────────────────────
class MsgType(str, Enum):
    TASK_ASSIGN = "TASK_ASSIGN"
    TASK_COMPLETE = "TASK_COMPLETE"
    TASK_FAILED = "TASK_FAILED"
    CONTEXT_REQUEST = "CONTEXT_REQUEST"
    CONTEXT_DELIVERY = "CONTEXT_DELIVERY"
    CONTEXT_WARN = "CONTEXT_WARN"
    CONTEXT_COMPRESS = "CONTEXT_COMPRESS"
    MODEL_RECOMMENDATION = "MODEL_RECOMMENDATION"
    HEARTBEAT = "HEARTBEAT"
    PLAN_REQUEST = "PLAN_REQUEST"
    PLAN_READY = "PLAN_READY"
    PROMPTS_READY = "PROMPTS_READY"


# ── Message schema ────────────────────────────────────────────────────────────
class Message(BaseModel):
    id: int
    created_at: str
    from_agent: str
    to_agent: str
    msg_type: MsgType
    payload: dict[str, Any]
    status: str
    priority: int


# ── DDL ───────────────────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    msg_type    TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    priority    INTEGER DEFAULT 5
);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id        TEXT PRIMARY KEY,
    model           TEXT,
    status          TEXT,
    tokens_used     INTEGER DEFAULT 0,
    context_pct     REAL DEFAULT 0.0,
    ram_mb          REAL DEFAULT 0.0,
    last_heartbeat  TIMESTAMP,
    metadata        TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    from_agent  TEXT NOT NULL,
    topic       TEXT NOT NULL,
    context     TEXT,
    response    TEXT,
    status      TEXT DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_messages_to_status
    ON messages (to_agent, status, priority);
"""


# ── MessageBus ────────────────────────────────────────────────────────────────
class MessageBus:
    def __init__(self, db_path: Path = BUS_DB_PATH):
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Create tables and open the connection."""
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_DDL)
        await self._conn.commit()
        _log.info("BUS INIT db=%s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # ── Core operations ───────────────────────────────────────────────────────

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        msg_type: MsgType,
        payload: dict[str, Any],
        priority: int = 5,
    ) -> int:
        """Send a message. Returns the new message id."""
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO messages (from_agent, to_agent, msg_type, payload, priority) "
                "VALUES (?, ?, ?, ?, ?)",
                (from_agent, to_agent, msg_type.value, json.dumps(payload), priority),
            )
            await self._conn.commit()
            msg_id = cur.lastrowid
        self._journal_message(
            {
                "id": msg_id,
                "created_at": datetime.now(UTC).isoformat(),
                "event": "send",
                "from_agent": from_agent,
                "to_agent": to_agent,
                "msg_type": msg_type.value,
                "payload": payload,
                "priority": priority,
            }
        )
        _log.debug("SEND id=%d %s→%s type=%s", msg_id, from_agent, to_agent, msg_type.value)
        return msg_id

    async def receive(
        self,
        agent_id: str,
        msg_types: list[MsgType] | None = None,
        limit: int = 10,
    ) -> list[Message]:
        """
        Fetch pending messages addressed to agent_id (or 'broadcast').
        Optionally filter by msg_types. Returns up to `limit` messages,
        ordered by priority ASC then created_at ASC.
        """
        placeholders = "?, ?"
        params: list[Any] = [agent_id, "broadcast"]

        type_clause = ""
        if msg_types:
            tp = ", ".join("?" * len(msg_types))
            type_clause = f"AND msg_type IN ({tp})"
            params.extend(t.value for t in msg_types)

        params.append(limit)

        rows = await self._conn.execute_fetchall(
            f"SELECT * FROM messages "
            f"WHERE to_agent IN ({placeholders}) AND status = 'pending' "
            f"{type_clause} "
            f"ORDER BY priority ASC, created_at ASC "
            f"LIMIT ?",
            params,
        )
        messages = [
            Message(
                id=r["id"],
                created_at=r["created_at"],
                from_agent=r["from_agent"],
                to_agent=r["to_agent"],
                msg_type=MsgType(r["msg_type"]),
                payload=json.loads(r["payload"]),
                status=r["status"],
                priority=r["priority"],
            )
            for r in rows
        ]
        if messages:
            for message in messages:
                self._journal_message(
                    {
                        "id": message.id,
                        "created_at": message.created_at,
                        "event": "receive",
                        "from_agent": message.from_agent,
                        "to_agent": message.to_agent,
                        "msg_type": message.msg_type.value,
                        "payload": message.payload,
                        "priority": message.priority,
                    }
                )
            _log.debug("RECV agent=%s count=%d", agent_id, len(messages))
        return messages

    def _journal_message(self, payload: dict[str, Any]) -> None:
        participants = {payload.get("from_agent", ""), payload.get("to_agent", "")}
        if any(name.startswith("agent1") for name in participants):
            target = _KNOWLEDGE_LOGS / "agent1"
        elif any(name.startswith("agent2") for name in participants):
            target = _KNOWLEDGE_LOGS / "agent2"
        else:
            target = _KNOWLEDGE_LOGS / "misc_agent"

        log_path = target / f"{datetime.now(UTC).date().isoformat()}.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    async def mark_read(self, message_id: int) -> None:
        """Mark a message as read so it won't be returned again."""
        async with self._lock:
            await self._conn.execute(
                "UPDATE messages SET status = 'read' WHERE id = ?", (message_id,)
            )
            await self._conn.commit()

    async def mark_processed(self, message_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE messages SET status = 'processed' WHERE id = ?", (message_id,)
            )
            await self._conn.commit()

    async def broadcast(
        self,
        from_agent: str,
        msg_type: MsgType,
        payload: dict[str, Any],
        priority: int = 5,
    ) -> int:
        """Send a message to all agents (to_agent = 'broadcast')."""
        return await self.send(from_agent, "broadcast", msg_type, payload, priority)

    async def get_pending_count(self, agent_id: str) -> int:
        """Return number of pending messages for agent_id."""
        row = await self._conn.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM messages "
            "WHERE to_agent IN (?, 'broadcast') AND status = 'pending'",
            (agent_id,),
        )
        return row[0]["cnt"] if row else 0

    # ── Knowledge requests ────────────────────────────────────────────────────

    async def create_knowledge_request(
        self, from_agent: str, topic: str, context: str | None = None
    ) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                "INSERT INTO knowledge_requests (from_agent, topic, context) VALUES (?, ?, ?)",
                (from_agent, topic, context),
            )
            await self._conn.commit()
            return cur.lastrowid

    async def fulfill_knowledge_request(
        self, request_id: int, response: str
    ) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE knowledge_requests SET response = ?, status = 'fulfilled' WHERE id = ?",
                (response, request_id),
            )
            await self._conn.commit()
