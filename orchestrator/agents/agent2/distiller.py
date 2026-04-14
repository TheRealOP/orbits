"""
agents/agent2/distiller.py — Context distillation for agents.

Wraps the existing orchestration.brain modules — does NOT reimplement them.
Adds agent-aware helpers on top:
  - distill_for_agent(): fetch + compress relevant context for a specific task
  - compress_conversation(): summarise a long conversation history
  - generate_handoff(): produce a full state handoff block

All Gemini calls go through orchestration.gemini.ask() (Gemini CLI cascade).
"""
import logging
import sys
from pathlib import Path

# Ensure repo root is importable
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestration.brain.distiller import distill
from orchestration.brain.synthesizer import synthesize
from orchestration.brain.tagger import tag
from orchestration.gemini import ask as gemini_ask

_log = logging.getLogger("orchestrator.agent2.distiller")


class ContextDistiller:
    def __init__(self, knowledge_store=None):
        # knowledge_store injected to avoid circular import; set after construction
        self._store = knowledge_store

    def set_store(self, store) -> None:
        self._store = store

    async def distill_for_agent(
        self, agent_id: str, task: str, max_tokens: int = 2000
    ) -> str:
        """
        Query the knowledge store for task-relevant context and compress
        it to fit within max_tokens (rough estimate: 1 token ≈ 4 chars).
        Returns a focused context string ready to prepend to a prompt.
        """
        if self._store is None:
            return ""

        chunks = await self._store.retrieve_memory(task, top_k=8)
        if not chunks:
            return ""

        # synthesize() already trims and formats chunks into a <memory> block
        raw = synthesize(task, chunks)

        # Trim to max_tokens (rough char estimate)
        char_limit = max_tokens * 4
        if len(raw) > char_limit:
            raw = raw[:char_limit] + "\n[context truncated]"

        _log.debug("distill_for_agent agent=%s chars=%d", agent_id, len(raw))
        return raw

    async def compress_conversation(
        self, history: list[dict], model: str = ""
    ) -> str:
        """
        Summarise a conversation history list into a compact handoff block.
        Called by ContextWindowGuard when an agent hits the compress threshold.

        history: [{"role": "user"|"assistant", "content": str}, ...]
        Returns a plain-text summary string.
        """
        if not history:
            return ""

        turns = "\n".join(
            f"{m['role'].upper()}: {m['content'][:500]}" for m in history[-20:]
        )
        prompt = (
            "Compress the following conversation into a dense handoff summary. "
            "Preserve: decisions made, key facts, current task state, what remains to do. "
            "Discard: pleasantries, repeated context, intermediate reasoning steps.\n\n"
            f"CONVERSATION:\n{turns}\n\n"
            "Output a compact summary (max 400 words):"
        )

        result = gemini_ask(prompt, label="Compress", timeout=60)
        if result:
            _log.debug("compress_conversation: compressed %d turns", len(history))
            return result

        # Fallback: last 3 turns raw
        fallback = "\n".join(
            f"{m['role'].upper()}: {m['content'][:300]}" for m in history[-3:]
        )
        return f"[Compression failed — last 3 turns]\n{fallback}"

    async def generate_handoff(self, agent_id: str, state: dict) -> str:
        """
        Generate a full handoff block describing current agent state.
        state: {"task": str, "completed_steps": list, "remaining_steps": list, "notes": str}
        """
        prompt = (
            f"Generate a concise handoff document for agent '{agent_id}'.\n\n"
            f"Current task: {state.get('task', 'unknown')}\n"
            f"Completed: {state.get('completed_steps', [])}\n"
            f"Remaining: {state.get('remaining_steps', [])}\n"
            f"Notes: {state.get('notes', '')}\n\n"
            "Output a structured handoff block (200 words max):"
        )
        result = gemini_ask(prompt, label="Handoff", timeout=60)
        return result or f"[Handoff for {agent_id}] Task: {state.get('task', 'unknown')}"
