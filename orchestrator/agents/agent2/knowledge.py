"""
agents/agent2/knowledge.py — Agent 2 main loop + KnowledgeStore.

Agent 2 is the single source of truth for context. It:
  - Runs a continuous async loop polling the message bus every 2 seconds
  - Handles CONTEXT_REQUEST → distill → CONTEXT_DELIVERY
  - Handles HEARTBEAT → update registry
  - Handles TASK_COMPLETE → curate → store in SLM (and Graphify when available)
  - Runs ctx_guard and researcher as background tasks

KnowledgeStore wraps orchestration.memory (SLM) for all storage operations.
Graphify calls go via subprocess with TODO markers for future Python API.

Usage:
    agent2 = Agent2(bus, registry)
    await agent2.run()
"""
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestration import memory as slm
from orchestration.brain.curator import should_remember
from orchestration.brain.tagger import tag

from orchestrator.core.bus import MessageBus, MsgType
from orchestrator.core.config import ORCH_DIR, REPO_ROOT
from orchestrator.core.registry import AgentRegistry
from orchestrator.agents.agent2.distiller import ContextDistiller
from orchestrator.agents.agent2.ctx_guard import ContextWindowGuard
from orchestrator.agents.agent2.model_oracle import ModelOracle
from orchestrator.agents.agent2.researcher import Researcher

_log = logging.getLogger("orchestrator.agent2")

_GRAPHIFY_BIN = REPO_ROOT / ".venv" / "bin" / "graphify"
_POLL_INTERVAL = 2  # seconds


# ── KnowledgeStore ────────────────────────────────────────────────────────────

class KnowledgeStore:
    """
    Unified interface over SuperLocalMemory (SLM) and Graphify.
    Agent 1 never calls this directly — only Agent 2 does.
    """

    # ── SLM operations ────────────────────────────────────────────────────────

    async def store_memory(self, key: str, content: str, tags: list[str]) -> bool:
        metadata = {"topic": key, "slug": key[:50].replace(" ", "_"), "tags": tags}
        return slm.remember(content, metadata=metadata)

    async def retrieve_memory(self, query: str, top_k: int = 5) -> list[dict]:
        return slm.recall(query, k=top_k)

    async def update_memory(self, key: str, content: str) -> bool:
        return slm.remember(content, metadata={"topic": key, "slug": key[:50].replace(" ", "_"), "tags": ["update"]})

    # ── Graphify operations ───────────────────────────────────────────────────
    # TODO: replace with graphify Python API when available

    def _graphify(self, args: list[str], input_data: str | None = None) -> str | None:
        """Run graphify CLI subprocess. Returns stdout or None on failure."""
        if not _GRAPHIFY_BIN.exists():
            return None
        try:
            result = subprocess.run(
                [str(_GRAPHIFY_BIN)] + args,
                input=input_data, capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:
            _log.warning("graphify subprocess error: %s", exc)
        return None

    async def add_node(self, node_id: str, node_type: str, properties: dict) -> bool:
        # TODO: graphify Python API
        payload = json.dumps({"id": node_id, "type": node_type, **properties})
        result = self._graphify(["add-node", "--json", payload])
        return result is not None

    async def add_edge(self, from_id: str, to_id: str, relation: str, properties: dict = {}) -> bool:
        # TODO: graphify Python API
        payload = json.dumps({"from": from_id, "to": to_id, "relation": relation, **properties})
        result = self._graphify(["add-edge", "--json", payload])
        return result is not None

    async def query_graph(self, query: str) -> list[dict]:
        # TODO: graphify Python API
        result = self._graphify(["query", query])
        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return [{"result": result}]
        return []

    async def get_related(self, node_id: str, depth: int = 2) -> list[dict]:
        # TODO: graphify Python API
        result = self._graphify(["related", node_id, f"--depth={depth}"])
        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return []
        return []

    # ── High-level helpers ────────────────────────────────────────────────────

    async def store_task_result(self, task_id: str, result: dict) -> None:
        content = json.dumps(result, indent=2)
        await self.store_memory(f"task_{task_id}", content, tags=["task_result"])
        # TODO: graphify Python API — link task result node to project node
        await self.add_node(f"task_{task_id}", "task_result", {"summary": str(result)[:200]})

    async def get_project_context(self, project_id: str) -> str:
        chunks = await self.retrieve_memory(f"project {project_id}", top_k=5)
        if not chunks:
            return ""
        return "\n".join(c["text"] for c in chunks)

    async def update_user_understanding(self, topic: str, level: str, notes: str) -> None:
        content = f"User understanding of '{topic}': level={level}. {notes}"
        await self.store_memory(f"user_profile_{topic}", content, tags=["user_profile"])

    async def get_user_profile(self) -> dict:
        profile_path = ORCH_DIR / "knowledge" / "user_profile.json"
        try:
            return json.loads(profile_path.read_text())
        except Exception:
            return {}


# ── Agent2 ────────────────────────────────────────────────────────────────────

class Agent2:
    AGENT_ID = "agent2"

    def __init__(self, bus: MessageBus, registry: AgentRegistry):
        self._bus = bus
        self._registry = registry
        self._store = KnowledgeStore()
        self._distiller = ContextDistiller(self._store)
        self._guard = ContextWindowGuard(bus, registry, self._distiller)
        self._oracle = ModelOracle()
        self._researcher = Researcher(self._store)
        self._researcher.set_store(self._store)
        self._distiller.set_store(self._store)

    async def run(self) -> None:
        await self._registry.register(self.AGENT_ID, "gemini-2.5-pro")
        _log.info("Agent2 started")

        # Start background tasks
        asyncio.create_task(self._guard.monitor_loop(), name="ctx_guard")

        # Main loop
        while True:
            try:
                await self._poll()
            except Exception as exc:
                _log.error("Agent2 poll error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _poll(self) -> None:
        messages = await self._bus.receive(self.AGENT_ID, limit=20)
        for msg in messages:
            await self._bus.mark_read(msg.id)
            try:
                await self._dispatch(msg)
            except Exception as exc:
                _log.error("dispatch error msg_id=%d type=%s: %s", msg.id, msg.msg_type, exc)

    async def _dispatch(self, msg) -> None:
        if msg.msg_type == MsgType.CONTEXT_REQUEST:
            await self._handle_context_request(msg)

        elif msg.msg_type == MsgType.HEARTBEAT:
            await self._registry.heartbeat(msg.from_agent, msg.payload)

        elif msg.msg_type == MsgType.TASK_COMPLETE:
            await self._handle_task_complete(msg)

        elif msg.msg_type == MsgType.MODEL_RECOMMENDATION:
            await self._handle_model_recommendation(msg)

        elif msg.msg_type == MsgType.TASK_FAILED:
            _log.warning("TASK_FAILED from=%s payload=%s", msg.from_agent, msg.payload)
            self._oracle.update_from_experience(
                msg.payload.get("task_type", "unknown"),
                msg.payload.get("model", ""),
                {"success": False},
            )

    async def _handle_context_request(self, msg) -> None:
        topic = msg.payload.get("topic", "")
        if topic == "__history__":
            return  # history requests handled inline by ctx_guard

        context = await self._distiller.distill_for_agent(
            msg.from_agent, topic, max_tokens=2000
        )
        # Also query graph for related nodes
        related = await self._store.get_related(topic, depth=1)
        if related:
            graph_ctx = "\n".join(str(r) for r in related[:5])
            context = f"{context}\n\n[Graph context]\n{graph_ctx}".strip()

        await self._bus.send(
            self.AGENT_ID, msg.from_agent, MsgType.CONTEXT_DELIVERY,
            {
                "topic": topic,
                "context": context,
                "request_id": msg.id,
                "context_packet": {
                    "topic": topic,
                    "constraints": msg.payload.get("constraints", {}),
                    "relevant_files": msg.payload.get("relevant_files", []),
                },
            },
            priority=2,
        )
        _log.debug("CONTEXT_DELIVERY → %s topic=%s chars=%d",
                   msg.from_agent, topic, len(context))

    async def _handle_model_recommendation(self, msg) -> None:
        task_type = msg.payload.get("task_type", "planning")
        constraints = msg.payload.get("constraints", {})
        packet = self._oracle.build_context_packet(task_type, constraints)
        await self._bus.send(
            self.AGENT_ID,
            msg.from_agent,
            MsgType.MODEL_RECOMMENDATION,
            {
                "request_id": msg.id,
                "task_type": task_type,
                "model": packet["recommended_model"],
                "context_packet": packet,
            },
            priority=3,
        )

    async def _handle_task_complete(self, msg) -> None:
        task_id = msg.payload.get("task_id", "unknown")
        output = msg.payload.get("output", "")
        task_type = msg.payload.get("task_type", "unknown")
        model = msg.payload.get("model", "")

        # Gate storage through curator
        store, metadata = should_remember("TASK_COMPLETE", output)
        if store and output:
            await self._store.store_task_result(task_id, {
                "output": output,
                "task_type": task_type,
                "model": model,
                "metadata": metadata,
            })
            _log.info("TASK_COMPLETE stored task_id=%s", task_id)

        # Update model oracle with outcome
        self._oracle.update_from_experience(
            task_type, model, {"success": True}
        )

        # Link result node to project in graph
        # TODO: graphify Python API — add richer project linkage
        await self._store.add_edge(f"task_{task_id}", "orbits_project", "belongs_to")
