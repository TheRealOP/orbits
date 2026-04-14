"""
workers/claude_worker.py — Worker that uses the Anthropic SDK.
"""
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.workers.base_worker import BaseWorker
from orchestrator.core.config import ANTHROPIC_API_KEY

_log = logging.getLogger("orchestrator.claude_worker")

_MAX_TOKENS = 4096


class ClaudeWorker(BaseWorker):
    async def execute(self, prompt: str, context: str) -> str:
        try:
            import anthropic
        except ImportError:
            return "[ClaudeWorker] anthropic package not installed"

        api_key = ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "[ClaudeWorker] ANTHROPIC_API_KEY not set"

        client = anthropic.Anthropic(api_key=api_key)
        messages = []
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}\n\n{prompt}"})
        else:
            messages.append({"role": "user", "content": prompt})

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                messages=messages,
            )
            output = response.content[0].text if response.content else ""
            self._tokens_used += response.usage.input_tokens + response.usage.output_tokens
            return output
        except Exception as exc:
            _log.error("ClaudeWorker API error: %s", exc)
            return f"[ClaudeWorker error] {exc}"
