"""
agents/agent2/researcher.py — Research subagent.

Uses Gemini to fill knowledge gaps identified during context distillation.
Stores findings back into SLM via KnowledgeStore.

Usage:
    researcher = Researcher(knowledge_store)
    await researcher.research("What is the best model for code generation in 2025?")
"""
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestration.gemini import ask as gemini_ask

_log = logging.getLogger("orchestrator.researcher")


class Researcher:
    def __init__(self, knowledge_store=None):
        self._store = knowledge_store

    def set_store(self, store) -> None:
        self._store = store

    async def research(self, topic: str, context: str = "") -> str | None:
        """
        Use Gemini to research a topic and store the finding.
        Returns the research result string, or None if Gemini unavailable.
        """
        prompt = (
            f"Research the following topic and provide a concise, factual summary "
            f"(max 300 words). Focus on actionable information.\n\n"
            f"Topic: {topic}\n"
        )
        if context:
            prompt += f"Additional context: {context}\n"

        result = gemini_ask(prompt, label="Research", timeout=90)
        if result and self._store:
            await self._store.store_memory(
                key=f"research_{topic[:50]}",
                content=result,
                tags=["research", "agent2"],
            )
            _log.info("research stored topic=%s chars=%d", topic, len(result))
        return result

    async def fill_gaps(self, recalled_chunks: list[dict], task: str) -> list[str]:
        """
        Identify and fill knowledge gaps given what was recalled for a task.
        Returns a list of new research findings.
        """
        if not recalled_chunks:
            result = await self.research(task)
            return [result] if result else []
        return []
