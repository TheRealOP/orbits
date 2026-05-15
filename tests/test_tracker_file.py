"""Tests for the file-based tracker adapter (orbit.tracker.file)."""
from __future__ import annotations

import yaml
from pathlib import Path
from datetime import datetime, timezone

import pytest

from orbit.tracker.file import FileTracker
from orbit.config import TrackerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cfg(path: Path) -> TrackerConfig:
    cfg = TrackerConfig()
    cfg.path = str(path)
    cfg.active_states = ["todo", "in-progress"]
    cfg.terminal_states = ["done", "cancelled"]
    return cfg


def write_tasks(path: Path, tasks: list[dict]) -> None:
    path.write_text(yaml.dump({"tasks": tasks}))


# ---------------------------------------------------------------------------
# fetch_candidate_issues
# ---------------------------------------------------------------------------

async def test_fetch_candidate_issues_returns_active(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Active", "state": "todo"},
        {"id": "2", "identifier": "T-2", "title": "Done", "state": "done"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert len(issues) == 1
    assert issues[0].identifier == "T-1"


async def test_fetch_candidate_excludes_terminal(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Active", "state": "todo"},
        {"id": "2", "identifier": "T-2", "title": "Cancelled", "state": "cancelled"},
        {"id": "3", "identifier": "T-3", "title": "Done", "state": "done"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert len(issues) == 1
    assert issues[0].identifier == "T-1"


async def test_fetch_candidate_includes_in_progress(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "WIP", "state": "in-progress"},
        {"id": "2", "identifier": "T-2", "title": "Todo", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert len(issues) == 2
    identifiers = {i.identifier for i in issues}
    assert identifiers == {"T-1", "T-2"}


# ---------------------------------------------------------------------------
# fetch_issues_by_states
# ---------------------------------------------------------------------------

async def test_fetch_by_states(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
        {"id": "2", "identifier": "T-2", "title": "Done", "state": "done"},
        {"id": "3", "identifier": "T-3", "title": "WIP", "state": "in-progress"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issues_by_states(["done"])
    assert len(issues) == 1
    assert issues[0].identifier == "T-2"


async def test_fetch_by_states_multiple(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
        {"id": "2", "identifier": "T-2", "title": "Done", "state": "done"},
        {"id": "3", "identifier": "T-3", "title": "Cancelled", "state": "cancelled"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issues_by_states(["done", "cancelled"])
    assert len(issues) == 2


async def test_fetch_by_states_empty_list_returns_empty(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issues_by_states([])
    assert issues == []


async def test_fetch_by_states_case_insensitive(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Done", "state": "Done"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issues_by_states(["done"])
    assert len(issues) == 1


# ---------------------------------------------------------------------------
# fetch_issue_states_by_ids
# ---------------------------------------------------------------------------

async def test_fetch_issue_states_by_ids(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
        {"id": "2", "identifier": "T-2", "title": "Done", "state": "done"},
        {"id": "3", "identifier": "T-3", "title": "WIP", "state": "in-progress"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issue_states_by_ids(["1", "3"])
    assert len(issues) == 2
    ids = {i.id for i in issues}
    assert ids == {"1", "3"}


async def test_fetch_issue_states_by_ids_empty_returns_empty(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issue_states_by_ids([])
    assert issues == []


async def test_fetch_issue_states_by_ids_missing_id_skipped(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    # "99" does not exist in file
    issues = await tracker.fetch_issue_states_by_ids(["1", "99"])
    assert len(issues) == 1
    assert issues[0].id == "1"


# ---------------------------------------------------------------------------
# Edge cases: missing or malformed file
# ---------------------------------------------------------------------------

async def test_missing_file_returns_empty(tmp_path):
    tasks_file = tmp_path / "nonexistent.yaml"
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues == []


async def test_missing_file_by_states_returns_empty(tmp_path):
    tasks_file = tmp_path / "nonexistent.yaml"
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_issues_by_states(["todo"])
    assert issues == []


async def test_empty_tasks_file(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues == []


async def test_tasks_file_with_empty_yaml(tmp_path):
    """A completely empty YAML file should not crash."""
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text("")
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues == []


async def test_tasks_file_with_list_at_root(tmp_path):
    """Root-level list (no 'tasks' wrapper) should also be supported."""
    tasks_file = tmp_path / "tasks.yaml"
    tasks_file.write_text(yaml.dump([
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
    ]))
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert len(issues) == 1


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

async def test_priority_parsed(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "High prio", "state": "todo", "priority": 1},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].priority == 1


async def test_priority_defaults_to_none(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "No prio", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].priority is None


async def test_priority_invalid_string_becomes_none(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Bad prio", "state": "todo", "priority": "high"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].priority is None


async def test_labels_lowercased(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Labeled", "state": "todo",
         "labels": ["Backend", "API", "High-Priority"]},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].labels == ["backend", "api", "high-priority"]


async def test_labels_empty_by_default(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "No labels", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].labels == []


async def test_blockers_parsed(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {
            "id": "1",
            "identifier": "T-1",
            "title": "Blocked",
            "state": "todo",
            "blocked_by": [
                {"id": "99", "identifier": "T-99", "state": "in-progress"},
            ],
        }
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert len(issues[0].blocked_by) == 1
    blocker = issues[0].blocked_by[0]
    assert blocker.id == "99"
    assert blocker.identifier == "T-99"
    assert blocker.state == "in-progress"


async def test_blockers_empty_by_default(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Free", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].blocked_by == []


async def test_multiple_blockers_parsed(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {
            "id": "1",
            "identifier": "T-1",
            "title": "Many blockers",
            "state": "todo",
            "blocked_by": [
                {"id": "2", "identifier": "T-2", "state": "todo"},
                {"id": "3", "identifier": "T-3", "state": "done"},
            ],
        }
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert len(issues[0].blocked_by) == 2


async def test_identifier_falls_back_to_id(tmp_path):
    """When identifier is absent, id is used as identifier."""
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "42", "title": "No identifier field", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].identifier == "42"


async def test_description_parsed(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Has desc", "state": "todo",
         "description": "Some description"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    issues = await tracker.fetch_candidate_issues()
    assert issues[0].description == "Some description"


# ---------------------------------------------------------------------------
# write_state
# ---------------------------------------------------------------------------

async def test_write_state_updates_file(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
        {"id": "2", "identifier": "T-2", "title": "WIP", "state": "in-progress"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    tracker.write_state("1", "done")

    # Re-load and verify
    data = yaml.safe_load(tasks_file.read_text())
    tasks = data["tasks"]
    task_1 = next(t for t in tasks if str(t["id"]) == "1")
    assert task_1["state"] == "done"
    # Other task should be unchanged
    task_2 = next(t for t in tasks if str(t["id"]) == "2")
    assert task_2["state"] == "in-progress"


async def test_write_state_sets_updated_at(tmp_path):
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    before = datetime.now(timezone.utc)
    tracker.write_state("1", "done")

    data = yaml.safe_load(tasks_file.read_text())
    task = data["tasks"][0]
    assert "updated_at" in task
    updated_at = datetime.fromisoformat(task["updated_at"])
    assert updated_at >= before


async def test_write_state_unknown_id_noop(tmp_path):
    """Writing a state for an ID not in the file should not crash or corrupt."""
    tasks_file = tmp_path / "tasks.yaml"
    write_tasks(tasks_file, [
        {"id": "1", "identifier": "T-1", "title": "Todo", "state": "todo"},
    ])
    tracker = FileTracker(make_cfg(tasks_file))
    tracker.write_state("999", "done")  # ID not present

    data = yaml.safe_load(tasks_file.read_text())
    task = data["tasks"][0]
    assert task["state"] == "todo"  # unchanged
