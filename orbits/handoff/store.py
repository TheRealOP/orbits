"""Deterministic handoff-store helpers for Orbits."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orbits.daemon.monitor import load_config

STORE_FILENAMES = {
    "plan": "plan.json",
    "handoff": "handoff.json",
    "decisions": "decisions.json",
    "session": "session.json",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_root(config: dict | None = None) -> Path:
    config = config or load_config()
    state_dir = Path(config["daemon"]["state_dir"])
    return state_dir if state_dir.is_absolute() else REPO_ROOT / state_dir


def _handoff_root(config: dict | None = None) -> Path:
    return _state_root(config) / "slm"


def _task_path(task_id: str, record_type: str, config: dict | None = None) -> Path:
    root = _handoff_root(config) / task_id
    root.mkdir(parents=True, exist_ok=True)
    return root / STORE_FILENAMES[record_type]


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _atomic_write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def write_task_record(task_id: str, record_type: str, payload: dict, config: dict | None = None) -> Path:
    record = {
        "task_id": task_id,
        "record_type": record_type,
        "updated_at": _utc_now(),
        "payload": payload,
    }
    return _atomic_write(_task_path(task_id, record_type, config), record)


def read_task_record(task_id: str, record_type: str, config: dict | None = None) -> dict | None:
    return _read_json(_task_path(task_id, record_type, config))


def write_session_owner(owner: str, task_id: str, config: dict | None = None) -> Path:
    return _atomic_write(
        _handoff_root(config) / STORE_FILENAMES["session"],
        {"owner": owner, "task_id": task_id, "updated_at": _utc_now()},
    )


def read_session_owner(config: dict | None = None) -> dict | None:
    return _read_json(_handoff_root(config) / STORE_FILENAMES["session"])


def set_pending_handoff(value: bool, config: dict | None = None) -> Path:
    config = config or load_config()
    status_path = _state_root(config) / "model_status.json"
    current = _read_json(status_path) or {}
    current["pending_handoff"] = value
    current["last_updated"] = _utc_now()
    return _atomic_write(status_path, current)
