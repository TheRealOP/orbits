"""
workers/gemini_worker.py — Worker that uses the Gemini CLI (orchestration.gemini).

Keeps the same CLI path as the rest of orbits — no separate SDK needed.
"""
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestration.gemini import ask as gemini_ask
from orchestration.brain.policy import DISTILLER_CASCADE

from orchestrator.workers.base_worker import BaseWorker

_log = logging.getLogger("orchestrator.gemini_worker")


class GeminiWorker(BaseWorker):
    async def execute(self, prompt: str, context: str) -> str:
        full_prompt = f"Context:\n{context}\n\n{prompt}" if context else prompt
        result = gemini_ask(full_prompt, label=f"Worker({self.model})", timeout=120,
                            _cascade=DISTILLER_CASCADE)
        if result is None:
            return "[GeminiWorker] Gemini unavailable or returned empty"
        return result
