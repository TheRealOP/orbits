"""Model-status monitor daemon for Orbits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestration.ram_manager import gate_launch

DEFAULT_CONFIG = {
    "daemon": {
        "poll_interval_seconds": 30,
        "state_dir": "orbits/state",
        "status_file": "orbits/state/model_status.json",
        "events_log": "orbits/state/model_status_events.jsonl",
        "claude_log_dir": "~/.claude/logs",
        "opencode_event_dir": "orbits/state/opencode",
        "pid_file": "orbits/state/model_status_daemon.pid",
    },
    "models": {
        "primary_orchestrator": "claude-sonnet-4-6",
        "primary_executor": "gpt-5.4",
        "interface": "claude-haiku-4-5",
        "interface_fallback": "gemini-2.5-flash",
    },
}
RATE_LIMIT_PATTERNS = ("rate_limit", "ratelimit", "429", "RateLimitError")


@dataclass(slots=True)
class OpenCodeStatus:
    status: str
    telemetry: str = "none"
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class ModelStatuses:
    claude_sonnet: str
    claude_haiku: str
    gpt_5_4: str
    interface_model: str
    last_updated: str
    pending_handoff: bool = False
    opencode_status: str = "unknown"
    opencode_telemetry: str = "none"
    opencode_input_tokens: int = 0
    opencode_cached_input_tokens: int = 0
    opencode_output_tokens: int = 0
    notes: str = ""


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Path | None = None) -> dict:
    config_path = config_path or (REPO_ROOT / "orbits" / "config.json")
    config = DEFAULT_CONFIG
    if config_path.exists():
        config = _deep_merge(DEFAULT_CONFIG, json.loads(config_path.read_text(encoding="utf-8")))
    daemon_cfg = config["daemon"]
    for key in ("state_dir", "status_file", "events_log", "claude_log_dir", "opencode_event_dir", "pid_file"):
        daemon_cfg[key] = str(daemon_cfg[key])
    return config


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(value: str) -> Path:
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else REPO_ROOT / path


def detect_claude(log_dir: Path, lookback_minutes: int = 10) -> str:
    if not log_dir.exists():
        return "unknown"
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    for path in sorted(log_dir.glob("**/*"), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pattern.lower() in content.lower() for pattern in RATE_LIMIT_PATTERNS):
            return "rate_limited"
    return "active"


def _opencode_process_active() -> bool:
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if "opencode" in name or "opencode" in cmdline:
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return False


def detect_interface(model: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            ["gemini", "--yolo", "Reply with the single word: pong", "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return "unknown"
    except subprocess.TimeoutExpired:
        return "error"
    return "active" if result.returncode == 0 else "error"


def summarize_opencode_events(lines: list[str]) -> OpenCodeStatus:
    total_input_tokens = 0
    total_cached_input_tokens = 0
    total_output_tokens = 0
    saw_usage = False
    saw_error = False
    plaintext_parts: list[str] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            plaintext_parts.append(line)
            continue

        if isinstance(event, dict) and event.get("error"):
            saw_error = True
            continue

        usage = event.get("usage") if isinstance(event, dict) else None
        if isinstance(usage, dict):
            saw_usage = True
            total_input_tokens += int(usage.get("input_tokens", 0) or 0)
            total_cached_input_tokens += int(usage.get("cached_input_tokens", 0) or 0)
            total_output_tokens += int(usage.get("output_tokens", 0) or 0)

    if saw_error:
        return OpenCodeStatus(status="error", telemetry="error")

    if saw_usage:
        return OpenCodeStatus(
            status="active",
            telemetry="jsonl",
            input_tokens=total_input_tokens,
            cached_input_tokens=total_cached_input_tokens,
            output_tokens=total_output_tokens,
        )

    estimated_tokens = 0
    if plaintext_parts:
        from orchestrator.core.metrics import MetricsTracker

        estimated_tokens = MetricsTracker().count_tokens("\n".join(plaintext_parts), "gpt-4o")
    return OpenCodeStatus(
        status="active" if (plaintext_parts or lines) else "unknown",
        telemetry="estimated" if estimated_tokens else "none",
        input_tokens=estimated_tokens,
    )


def _latest_jsonl_file(log_dir: Path, lookback_minutes: int = 10) -> Path | None:
    if not log_dir.exists():
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    for path in sorted(log_dir.glob("**/*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified >= cutoff:
            return path
    return None


def detect_opencode(log_dir: Path | None = None, lookback_minutes: int = 10) -> OpenCodeStatus:
    process_active = _opencode_process_active()
    log_path = _latest_jsonl_file(log_dir, lookback_minutes) if log_dir else None
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines() if log_path else []
    summary = summarize_opencode_events(lines)

    if summary.status == "error":
        return summary
    if process_active and summary.telemetry == "none":
        return OpenCodeStatus(status="active", telemetry="estimated")
    if process_active and summary.status == "unknown":
        return OpenCodeStatus(status="active", telemetry="estimated")
    if not process_active and summary.telemetry == "none":
        return OpenCodeStatus(status="unknown", telemetry="none")
    return summary

def _read_status(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _status_signature(payload: dict) -> dict:
    return {
        "claude_sonnet": payload.get("claude_sonnet"),
        "claude_haiku": payload.get("claude_haiku"),
        "gpt_5_4": payload.get("gpt_5_4"),
        "interface_model": payload.get("interface_model"),
        "pending_handoff": payload.get("pending_handoff", False),
        "opencode_status": payload.get("opencode_status"),
        "opencode_telemetry": payload.get("opencode_telemetry"),
        "opencode_input_tokens": payload.get("opencode_input_tokens"),
        "opencode_cached_input_tokens": payload.get("opencode_cached_input_tokens"),
        "opencode_output_tokens": payload.get("opencode_output_tokens"),
        "notes": payload.get("notes", ""),
    }


def write_status(statuses: ModelStatuses, config: dict) -> None:
    status_path = _resolve_path(config["daemon"]["status_file"])
    events_path = _resolve_path(config["daemon"]["events_log"])
    status_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)

    previous = _read_status(status_path)
    payload = asdict(statuses)
    if previous and previous.get("pending_handoff") and not payload.get("pending_handoff", False):
        payload["pending_handoff"] = True
    tmp_path = status_path.with_suffix(status_path.suffix + f".{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(status_path)

    changed = previous is None or _status_signature(previous) != _status_signature(payload)
    if changed:
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


class MonitorDaemon:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()

    def _ram_preflight(self) -> tuple[bool, str]:
        decision = gate_launch(essential=True)
        if decision.state == "critical":
            return False, f"RAM critical at {decision.total_used_gb} GB; monitor will not start."
        return True, ""

    def run_once(self) -> ModelStatuses:
        allowed, note = self._ram_preflight()
        if not allowed:
            existing = _read_status(_resolve_path(self.config["daemon"]["status_file"])) or {}
            statuses = ModelStatuses(
                claude_sonnet="unknown",
                claude_haiku="unknown",
                gpt_5_4="unknown",
                interface_model="unknown",
                last_updated=_utc_now(),
                pending_handoff=bool(existing.get("pending_handoff", False)),
                notes=note,
            )
            write_status(statuses, self.config)
            return statuses

        claude_state = detect_claude(_resolve_path(self.config["daemon"]["claude_log_dir"]))
        interface_state = detect_interface(self.config["models"]["interface_fallback"])
        opencode_metrics = detect_opencode(
            _resolve_path(self.config["daemon"]["opencode_event_dir"]),
            self.config["daemon"].get("opencode_log_lookback_minutes", 10),
        )
        existing = _read_status(_resolve_path(self.config["daemon"]["status_file"])) or {}
        pending_handoff = bool(existing.get("pending_handoff", False))

        notes = []
        if claude_state == "rate_limited":
            notes.append("Claude rate limit signal detected from logs.")
        if opencode_metrics.status != "active":
            notes.append(f"OpenCode status={opencode_metrics.status}.")
        elif opencode_metrics.telemetry != "none":
            notes.append(
                "OpenCode telemetry="
                f"{opencode_metrics.telemetry} input={opencode_metrics.input_tokens} "
                f"cached={opencode_metrics.cached_input_tokens} output={opencode_metrics.output_tokens}."
            )
        if interface_state != "active":
            notes.append(f"Interface probe status={interface_state}.")
        if pending_handoff:
            notes.append("Pending handoff is active.")

        statuses = ModelStatuses(
            claude_sonnet=claude_state,
            claude_haiku="active" if claude_state == "active" else claude_state,
            gpt_5_4=opencode_metrics.status,
            interface_model=interface_state,
            opencode_status=opencode_metrics.status,
            opencode_telemetry=opencode_metrics.telemetry,
            opencode_input_tokens=opencode_metrics.input_tokens,
            opencode_cached_input_tokens=opencode_metrics.cached_input_tokens,
            opencode_output_tokens=opencode_metrics.output_tokens,
            last_updated=_utc_now(),
            pending_handoff=pending_handoff,
            notes=" ".join(notes),
        )
        write_status(statuses, self.config)
        return statuses

    def run(self, iterations: int | None = None) -> None:
        interval = float(self.config["daemon"]["poll_interval_seconds"])
        count = 0
        while iterations is None or count < iterations:
            statuses = self.run_once()
            print(json.dumps(asdict(statuses), indent=2))
            count += 1
            if iterations is None or count < iterations:
                time.sleep(interval)


def _pid_file(config: dict) -> Path:
    return _resolve_path(config["daemon"]["pid_file"])


def _read_pid(config: dict) -> int | None:
    path = _pid_file(config)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_pid(config: dict, pid: int) -> Path:
    path = _pid_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")
    return path


def _clear_pid(config: dict) -> None:
    path = _pid_file(config)
    if path.exists():
        path.unlink()


def daemon_status(config: dict) -> dict:
    pid = _read_pid(config)
    alive = bool(pid and _pid_is_alive(pid))
    if pid and not alive:
        _clear_pid(config)
        pid = None
    return {"running": alive, "pid": pid}


def start_daemon(config: dict) -> dict:
    status = daemon_status(config)
    if status["running"]:
        return {"started": False, "reason": "already_running", "pid": status["pid"]}

    env = os.environ.copy()
    process = subprocess.Popen(
        [sys.executable, "-m", "orbits.daemon.monitor"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _write_pid(config, process.pid)
    return {"started": True, "pid": process.pid}


def stop_daemon(config: dict) -> dict:
    pid = _read_pid(config)
    if not pid:
        return {"stopped": False, "reason": "not_running"}
    if not _pid_is_alive(pid):
        _clear_pid(config)
        return {"stopped": False, "reason": "stale_pid"}
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not _pid_is_alive(pid):
            _clear_pid(config)
            return {"stopped": True, "pid": pid}
        time.sleep(0.1)
    return {"stopped": False, "reason": "timeout", "pid": pid}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orbits model-status monitor daemon")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--start", action="store_true", help="Start monitor as a background daemon")
    parser.add_argument("--stop", action="store_true", help="Stop the background daemon")
    parser.add_argument("--status", action="store_true", help="Show daemon running status")
    parser.add_argument("--iterations", type=int, default=None, help="Run a fixed number of cycles")
    parser.add_argument("--config", type=Path, default=None, help="Override config path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config) if args.config else load_config()
    if args.start:
        print(json.dumps(start_daemon(config), indent=2))
        return 0
    if args.stop:
        print(json.dumps(stop_daemon(config), indent=2))
        return 0
    if args.status:
        print(json.dumps(daemon_status(config), indent=2))
        return 0
    daemon = MonitorDaemon(config)
    if args.once:
        print(json.dumps(asdict(daemon.run_once()), indent=2))
        return 0
    daemon.run(iterations=args.iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
