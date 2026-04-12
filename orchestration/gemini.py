"""
gemini.py — shared Gemini CLI helper with model cascade.

Ported pattern from: Financial assistant/agent1.py and linker.py

Exposes:
    ask(prompt, *, label="", timeout=120) -> str | None
    ask_json(prompt, *, label="", timeout=120) -> dict | None

The cascade tries each model in order and falls through on rate-limit
or non-zero exit. Returns None if every tier fails.

Set GEMINI_DISABLED=1 to skip all Gemini calls (offline dev / testing).
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

# Model cascade: best first, fall back on rate limit / error.
# Note: this CLI (gemini-cli v0.24.0) uses the Google Code Assist endpoint
# (cloudcode-pa.googleapis.com). Only these model names are valid here:
#   gemini-2.5-pro   — highest quality, hits capacity at peak times
#   gemini-2.5-flash — reliable fallback
#   None             — omit --model flag, CLI uses its default (currently 2.5-pro)
MODEL_CASCADE = [
    {"name": "gemini-2.5-pro",   "label": "Pro"},
    {"name": "gemini-2.5-flash", "label": "Flash"},
    {"name": None,               "label": "Default"},  # no --model flag
]


def _spinner(stop_event: threading.Event, label: str) -> None:
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f"\r  {chars[i % len(chars)]} {label}...")
        sys.stdout.flush()
        time.sleep(0.1)
        i += 1
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()


def ask(
    prompt: str,
    *,
    label: str = "",
    timeout: int = 120,
    _cascade: list[dict] | None = None,
) -> str | None:
    """
    Run prompt through the Gemini CLI cascade.
    Returns the stripped stdout string, or None if every tier fails.
    Uses a spinner on stdout so the caller sees progress.
    """
    from orchestration.config import is_gemini_disabled
    if is_gemini_disabled():
        return None

    cascade = _cascade or MODEL_CASCADE
    for model in cascade:
        display = label or "Gemini"
        stop = threading.Event()
        t = threading.Thread(
            target=_spinner,
            args=(stop, f"{display} ({model['label']})"),
            daemon=True,
        )
        t.start()
        try:
            # --yolo: auto-approve tool actions (prevents hanging for approval)
            # Use positional prompt (deprecated -p removed in future versions)
            cmd = ["gemini", "--yolo", prompt]
            if model["name"] is not None:
                cmd += ["--model", model["name"]]
            # Use Popen + process group so timeout kills the full node tree
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
            try:
                stdout, _ = proc.communicate(timeout=timeout)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise
            stop.set(); t.join()
            result_stdout = stdout.strip()
            if returncode == 0:
                if result_stdout:
                    return result_stdout
                # empty output — treat as soft failure, try next tier
                print(f"  ⚠ {model['label']} returned empty, falling back...")
            else:
                print(f"  ⚠ {model['label']} error (exit {returncode}), falling back...")
        except subprocess.TimeoutExpired:
            stop.set(); t.join()
            print(f"  ⚠ {model['label']} timed out, falling back...")
        except FileNotFoundError:
            stop.set(); t.join()
            print("  ✗ 'gemini' CLI not found — set GEMINI_DISABLED=1 or install it")
            return None  # no point trying further tiers
        except Exception as e:
            stop.set(); t.join()
            print(f"  ⚠ {model['label']} unexpected error: {e}")

    return None


def ask_json(
    prompt: str,
    *,
    label: str = "",
    timeout: int = 120,
    _cascade: list[dict] | None = None,
) -> dict | None:
    """
    Like ask(), but expects JSON output and parses it.
    Strips markdown code fences (```json … ```) if present.
    Returns parsed dict, or None on failure.
    """
    raw = ask(prompt, label=label, timeout=timeout, _cascade=_cascade)
    if raw is None:
        return None
    # strip markdown fences
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first and last fence lines
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
