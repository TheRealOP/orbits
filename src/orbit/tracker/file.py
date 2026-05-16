"""File-based tracker adapter — Orbit §2.5."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..config import TrackerConfig
from ..models import BlockerRef, Issue
from .base import TrackerAdapter

log = logging.getLogger(__name__)


def _parse_issue(raw: dict) -> Issue:
    created_raw = raw.get("created_at")
    updated_raw = raw.get("updated_at")

    def _dt(val: str | None) -> datetime | None:
        if not val:
            return None
        try:
            return datetime.fromisoformat(str(val))
        except ValueError:
            return None

    blockers = []
    for b in raw.get("blocked_by", []):
        blockers.append(BlockerRef(
            id=str(b.get("id", "")),
            identifier=str(b.get("identifier", "")),
            state=b.get("state"),
        ))

    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else None
    except (TypeError, ValueError):
        priority = None

    return Issue(
        id=str(raw.get("id", "")),
        identifier=str(raw.get("identifier", raw.get("id", ""))),
        title=str(raw.get("title", "")),
        description=raw.get("description"),
        priority=priority,
        state=str(raw.get("state", "")),
        labels=[s.lower() for s in raw.get("labels", [])],
        blocked_by=blockers,
        created_at=_dt(created_raw),
        updated_at=_dt(updated_raw),
    )


class FileTracker(TrackerAdapter):
    """Reads tasks from a YAML file; writes state changes back."""

    def __init__(self, cfg: TrackerConfig):
        self._path = Path(cfg.path)
        self._active_states = [s.lower() for s in cfg.active_states]
        self._terminal_states = [s.lower() for s in cfg.terminal_states]

    def _load_tasks(self) -> list[dict]:
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("file_tracker_not_found path=%s", self._path)
            return []
        except yaml.YAMLError as exc:
            log.error("file_tracker_parse_error path=%s error=%s", self._path, exc)
            return []
        if isinstance(data, dict):
            return data.get("tasks", [])
        if isinstance(data, list):
            return data
        return []

    async def fetch_candidate_issues(self) -> list[Issue]:
        tasks = self._load_tasks()
        issues = []
        for raw in tasks:
            state = str(raw.get("state", "")).lower()
            if state in self._active_states and state not in self._terminal_states:
                issues.append(_parse_issue(raw))
        return issues

    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        if not state_names:
            return []
        normalized = {s.lower() for s in state_names}
        tasks = self._load_tasks()
        return [_parse_issue(r) for r in tasks if str(r.get("state", "")).lower() in normalized]

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        if not issue_ids:
            return []
        id_set = set(issue_ids)
        tasks = self._load_tasks()
        return [_parse_issue(r) for r in tasks if str(r.get("id", "")) in id_set]

    def write_state(self, issue_id: str, new_state: str) -> None:
        tasks = self._load_tasks()
        for raw in tasks:
            if str(raw.get("id", "")) == issue_id:
                raw["state"] = new_state
                raw["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._path.write_text(
                yaml.dump({"tasks": tasks}, default_flow_style=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("file_tracker_write_failed error=%s", exc)

    async def transition_issue_state(self, issue_id: str, state_name: str) -> bool:
        self.write_state(issue_id, state_name)
        return True
