"""
config.py — reads the orbit.json configuration file at the repository root.
"""
import json
import os
from pathlib import Path

def load_config() -> dict[str, any]:
    root = Path(__file__).parent.parent
    orbit_json_file = root / "orbit.json"
    if orbit_json_file.exists():
        try:
            with open(orbit_json_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def should_disable_session_recall() -> bool:
    if os.environ.get("ORBITS_NO_SESSION_RECALL") == "1": return True
    config = load_config()
    return config.get("memory", {}).get("triggers", {}).get("session_recall") is False

def should_disable_prompt_inject() -> bool:
    if os.environ.get("ORBITS_NO_PROMPT_INJECT") == "1": return True
    config = load_config()
    return config.get("memory", {}).get("triggers", {}).get("prompt_inject") is False

def should_disable_auto_remember() -> bool:
    if os.environ.get("ORBITS_NO_AUTO_REMEMBER") == "1": return True
    config = load_config()
    return config.get("memory", {}).get("triggers", {}).get("auto_remember") is False

def is_gemini_disabled() -> bool:
    if os.environ.get("GEMINI_DISABLED") == "1": return True
    config = load_config()
    return config.get("memory", {}).get("brain_disabled") is True
