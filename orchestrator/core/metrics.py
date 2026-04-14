"""
core/metrics.py — Token counting (tiktoken) and RAM monitoring (psutil).

Usage:
    tracker = MetricsTracker()
    n = tracker.count_tokens("hello world", "claude-sonnet-4-6")
    pct = tracker.get_context_fill_pct(n, "claude-sonnet-4-6")
    mb = tracker.get_ram_usage_mb(os.getpid())
"""
import os
import logging
from functools import lru_cache

import psutil
import tiktoken

_log = logging.getLogger("orchestrator.metrics")

# Context window sizes in tokens
CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    # Google (via Gemini CLI)
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    # DeepSeek
    "deepseek-coder": 128_000,
    # Mistral
    "codestral": 32_000,
}

# Fallback context window for unknown models
_DEFAULT_CONTEXT_WINDOW = 128_000

# tiktoken encoding to use for all models (cl100k_base covers GPT-4 / Claude range)
_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _get_encoding():
    return tiktoken.get_encoding(_ENCODING_NAME)


class MetricsTracker:
    def count_tokens(self, text: str, model: str = "") -> int:
        """Estimate token count using tiktoken cl100k_base encoding."""
        try:
            enc = _get_encoding()
            return len(enc.encode(text))
        except Exception as exc:
            _log.warning("count_tokens failed: %s", exc)
            # Rough fallback: ~4 chars per token
            return max(1, len(text) // 4)

    def get_context_fill_pct(self, tokens_used: int, model: str) -> float:
        """Return fraction of context window used (0.0–1.0)."""
        window = CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)
        return min(1.0, tokens_used / window)

    def get_ram_usage_mb(self, pid: int) -> float:
        """Return RSS memory usage of the given process in MB."""
        try:
            proc = psutil.Process(pid)
            return proc.memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            _log.warning("get_ram_usage_mb pid=%d: %s", pid, exc)
            return 0.0

    async def report_metrics(self, agent_id: str, tokens_used: int = 0, model: str = "") -> dict:
        """Compile a metrics dict suitable for registry.heartbeat()."""
        pid = os.getpid()
        context_pct = self.get_context_fill_pct(tokens_used, model)
        ram_mb = self.get_ram_usage_mb(pid)
        return {
            "agent_id": agent_id,
            "tokens_used": tokens_used,
            "context_pct": context_pct,
            "ram_mb": ram_mb,
            "pid": pid,
        }
