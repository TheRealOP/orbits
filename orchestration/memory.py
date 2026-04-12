"""
memory.py — thin wrapper around the SuperLocalMemory CLI (slm).

Ported from: Financial assistant/memory.py
Adapts _check_available to prefer .venv/bin/slm when slm isn't on PATH.

Usage:
    import orchestration.memory as memory
    memory.remember("some fact", metadata={"topic": "...", "slug": "...", "tags": [...]})
    results = memory.recall("query", k=8)
    # results: list of {"text": str, "score": float} or [] on failure

Setup (handled by scripts/bootstrap.sh):
    pip install -e vendor/superlocalmemory
    slm setup && slm mode a && slm doctor
"""
import json
import os
import subprocess
from pathlib import Path

# Prefer .venv/bin/slm (orbits-local install) over system PATH
_REPO_ROOT = Path(__file__).parent.parent
_VENV_SLM  = str(_REPO_ROOT / ".venv" / "bin" / "slm")

_SLM_AVAILABLE: bool | None = None  # cached after first check
_SLM_CMD: str | None = None


def _find_slm() -> str | None:
    """Return the first reachable slm binary path, or None."""
    candidates = [_VENV_SLM, "slm"]
    for cmd in candidates:
        try:
            r = subprocess.run(
                [cmd, "status"],
                capture_output=True, timeout=5
            )
            if r.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _check_available() -> bool:
    global _SLM_AVAILABLE, _SLM_CMD
    if _SLM_AVAILABLE is None:
        _SLM_CMD = _find_slm()
        _SLM_AVAILABLE = _SLM_CMD is not None
    return _SLM_AVAILABLE


def _slm_cmd() -> str:
    _check_available()
    return _SLM_CMD or "slm"


def remember(text: str, metadata: dict | None = None) -> bool:
    """
    Store text in SuperLocalMemory.
    metadata dict is embedded as a structured prefix so it's searchable
    (slm has no --metadata flag):
        [topic: <topic>] [slug: <slug>] [tags: <tag1>, <tag2>]
    Silently no-ops if slm is not installed.
    Returns True on success, False on failure.
    """
    if not _check_available():
        return False
    try:
        if metadata:
            topic = metadata.get("topic", "")
            slug  = metadata.get("slug", "")
            tags  = metadata.get("tags", [])
            if isinstance(tags, list):
                tags = ", ".join(tags)
            prefix  = f"[topic: {topic}] [slug: {slug}] [tags: {tags}]\n"
            payload = prefix + text
        else:
            payload = text

        result = subprocess.run(
            [_slm_cmd(), "remember", payload],
            capture_output=True, text=True, timeout=180,  # model load on cold start can take >30s
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def recall(query: str, k: int = 5) -> list[dict]:
    """
    Retrieve top-k relevant memories for query.
    Returns list of {"text": str, "score": float}.
    Returns [] on failure or if slm is not installed.
    """
    if not _check_available():
        return []
    try:
        result = subprocess.run(
            [_slm_cmd(), "recall", query, "--json"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return []
        envelope = json.loads(result.stdout)
        # slm v3 schema: {"data": {"results": [{"content": str, "score": float, ...}]}}
        results = envelope.get("data", {}).get("results", [])
        normalised = [
            {"text": item.get("content", ""), "score": item.get("score", 0.0)}
            for item in results
        ]
        return normalised[:k]
    except Exception:
        return []
