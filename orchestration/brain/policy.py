"""
policy.py — single source of truth for which Gemini tier each brain module uses.

Edit here to tune cost vs. quality across the whole pipeline.

HIGH_FREQUENCY paths (called on every tool use): use flash.
LOW_FREQUENCY paths (called once per session / user request): use pro.

Note: gemini-cli v0.24.0 (Code Assist endpoint) only supports:
  gemini-2.5-pro   (Pro)     — best quality, may hit capacity
  gemini-2.5-flash (Flash)   — reliable, fast
  None             (Default) — omit --model flag, CLI chooses
"""
from orchestration.gemini import MODEL_CASCADE

# Tiers by index into MODEL_CASCADE: 0=Pro, 1=Flash, 2=Default(no --model)
_PRO     = MODEL_CASCADE[0:1]   # gemini-2.5-pro
_FLASH   = MODEL_CASCADE[1:2]   # gemini-2.5-flash
_DEFAULT = MODEL_CASCADE[2:3]   # no --model flag (CLI default)
_ALL     = MODEL_CASCADE        # full cascade with fallback

# Per-module tier assignments
CURATOR_CASCADE    = _FLASH  # high-frequency: runs on every PostToolUse
TAGGER_CASCADE     = _FLASH  # high-frequency: runs during ingestion
DISTILLER_CASCADE  = _FLASH       # medium: runs once per ingested note
SYNTHESIZER_CASCADE= _FLASH       # medium: runs per recall injection
LINKER_CASCADE     = _ALL         # low: runs on explicit /knowledge-sync
