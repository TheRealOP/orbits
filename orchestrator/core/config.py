"""
core/config.py — Central configuration for the orchestrator.

Loads from .env (via python-dotenv) and exposes typed constants.
Falls back to safe defaults if variables are not set.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the orchestrator directory, then from repo root as fallback
_ORCH_DIR = Path(__file__).parent.parent
_REPO_ROOT = _ORCH_DIR.parent

load_dotenv(_ORCH_DIR / ".env", override=False)
load_dotenv(_REPO_ROOT / ".env", override=False)

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ── Model defaults ────────────────────────────────────────────────────────────
DEFAULT_WORKER_MODEL: str = os.getenv("DEFAULT_WORKER_MODEL", "claude-haiku-4-5")
AGENT2_MODEL: str = os.getenv("AGENT2_MODEL", "gemini-2.5-pro")

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_WORKER_TOKENS: int = int(os.getenv("MAX_WORKER_TOKENS", "4000"))
CONTEXT_WARN_THRESHOLD: float = float(os.getenv("CONTEXT_WARN_THRESHOLD", "0.75"))
CONTEXT_HARD_LIMIT: float = float(os.getenv("CONTEXT_HARD_LIMIT", "0.90"))

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT: Path = _REPO_ROOT
ORCH_DIR: Path = _ORCH_DIR
BUS_DB_PATH: Path = _ORCH_DIR / "bus.db"
LOGS_DIR: Path = _ORCH_DIR / "logs"
KNOWLEDGE_DIR: Path = _ORCH_DIR / "knowledge"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Ensure directories exist
LOGS_DIR.mkdir(exist_ok=True)
KNOWLEDGE_DIR.mkdir(exist_ok=True)
