"""Shared fixtures for Orbit test suite."""
import pytest
from pathlib import Path
from orbit.models import Issue


@pytest.fixture
def tmp_workflow_dir(tmp_path):
    """Return a temp directory (no files created)."""
    return tmp_path


@pytest.fixture
def make_workflow_file(tmp_path):
    """Factory: call with content string, returns path to ORBIT.md."""
    def _make(content: str, filename: str = "ORBIT.md") -> Path:
        f = tmp_path / filename
        f.write_text(content)
        return f
    return _make


@pytest.fixture
def simple_issue():
    return Issue(id="1", identifier="TASK-1", title="Test task", state="Todo")


@pytest.fixture
def simple_workflow_file(tmp_path):
    content = '''---
tracker:
  kind: file
  path: ./tasks.yaml
agents:
  default: codex
runner:
  kind: cli
  commands:
    codex: "echo done"
---
You are working on {{ issue.identifier }}: {{ issue.title }}
'''
    f = tmp_path / "ORBIT.md"
    f.write_text(content)
    # Also create tasks.yaml
    tasks = tmp_path / "tasks.yaml"
    tasks.write_text("tasks:\n  - id: '1'\n    identifier: TASK-1\n    title: Test\n    state: todo\n")
    return f


@pytest.fixture
def tasks_yaml(tmp_path):
    tasks = tmp_path / "tasks.yaml"
    tasks.write_text("tasks:\n  - id: '1'\n    identifier: TASK-1\n    title: Test\n    state: todo\n")
    return tasks
