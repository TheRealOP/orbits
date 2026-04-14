"""
workers/openai_worker.py — Worker that uses the OpenAI SDK.
Also handles DeepSeek (OpenAI-compatible endpoint).
"""
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.workers.base_worker import BaseWorker
from orchestrator.core.config import OPENAI_API_KEY

_log = logging.getLogger("orchestrator.openai_worker")

_MAX_TOKENS = 4096

# DeepSeek uses an OpenAI-compatible endpoint
_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


class OpenAIWorker(BaseWorker):
    async def execute(self, prompt: str, context: str) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            return "[OpenAIWorker] openai package not installed"

        is_deepseek = "deepseek" in self.model.lower()
        if is_deepseek:
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            client = OpenAI(api_key=api_key, base_url=_DEEPSEEK_BASE_URL)
        else:
            api_key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
            client = OpenAI(api_key=api_key)

        messages = []
        if context:
            messages.append({"role": "system", "content": f"Context:\n{context}"})
        messages.append({"role": "user", "content": prompt})

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=_MAX_TOKENS,
            )
            output = response.choices[0].message.content or ""
            if response.usage:
                self._tokens_used += response.usage.total_tokens
            return output
        except Exception as exc:
            _log.error("OpenAIWorker API error: %s", exc)
            return f"[OpenAIWorker error] {exc}"
