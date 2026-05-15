"""Tests for orbit.runner.cli — CLIRunner."""
import pytest
from pathlib import Path
from orbit.runner.cli import CLIRunner
from orbit.runner.base import RunnerEvent, RunnerEventType
from orbit.config import RunnerConfig
from orbit.models import Issue


def make_cfg(commands=None, timeout_ms=5000, stall_timeout_ms=10000) -> RunnerConfig:
    cfg = RunnerConfig()
    cfg.kind = "cli"
    cfg.commands = commands or {"test_agent": "cat"}  # cat reads stdin and echoes
    cfg.timeout_ms = timeout_ms
    cfg.stall_timeout_ms = stall_timeout_ms
    return cfg


def simple_issue() -> Issue:
    return Issue(id="1", identifier="T-1", title="Test", state="todo")


@pytest.mark.asyncio
async def test_successful_run_emits_session_started_and_turn_completed(tmp_path):
    events = []
    cfg = make_cfg({"echo_agent": "echo hello"})
    runner = CLIRunner(cfg)
    issue = simple_issue()
    exit_code = await runner.run("echo_agent", "prompt", tmp_path, on_event=events.append)
    assert exit_code == 0
    event_types = [e.event for e in events]
    assert RunnerEventType.SESSION_STARTED in event_types
    assert RunnerEventType.TURN_COMPLETED in event_types


@pytest.mark.asyncio
async def test_failed_command_emits_turn_failed(tmp_path):
    events = []
    cfg = make_cfg({"fail_agent": "exit 1"})
    runner = CLIRunner(cfg)
    exit_code = await runner.run("fail_agent", "prompt", tmp_path, on_event=events.append)
    assert exit_code != 0
    event_types = [e.event for e in events]
    assert RunnerEventType.TURN_FAILED in event_types


@pytest.mark.asyncio
async def test_timeout_emits_turn_timeout(tmp_path):
    events = []
    cfg = make_cfg({"slow_agent": "sleep 60"}, timeout_ms=200)
    runner = CLIRunner(cfg)
    exit_code = await runner.run("slow_agent", "prompt", tmp_path, on_event=events.append)
    event_types = [e.event for e in events]
    assert RunnerEventType.TURN_TIMEOUT in event_types


@pytest.mark.asyncio
async def test_prompt_passed_via_stdin(tmp_path):
    # cat reads stdin and echoes it to stdout; we check STREAMING event contains our prompt
    events = []
    cfg = make_cfg({"cat_agent": "cat"})
    runner = CLIRunner(cfg)
    exit_code = await runner.run("cat_agent", "hello from prompt", tmp_path, on_event=events.append)
    streaming = [e.message for e in events if e.event == RunnerEventType.STREAMING]
    assert any("hello from prompt" in m for m in streaming)


@pytest.mark.asyncio
async def test_unknown_agent_raises(tmp_path):
    cfg = make_cfg({"only_agent": "echo"})
    runner = CLIRunner(cfg)
    with pytest.raises(ValueError, match="runner_command_not_found"):
        await runner.run("missing_agent", "prompt", tmp_path, on_event=lambda e: None)


@pytest.mark.asyncio
async def test_cwd_is_workspace_path(tmp_path):
    # Command prints cwd; verify it matches workspace_path
    events = []
    cfg = make_cfg({"pwd_agent": "pwd"})
    runner = CLIRunner(cfg)
    await runner.run("pwd_agent", "", tmp_path, on_event=events.append)
    streaming = [e.message for e in events if e.event == RunnerEventType.STREAMING]
    assert any(str(tmp_path.resolve()) in m for m in streaming)


@pytest.mark.asyncio
async def test_startup_failed_when_command_not_found(tmp_path):
    events = []
    cfg = make_cfg({"bad_agent": "this_command_does_not_exist_xyz123"})
    runner = CLIRunner(cfg)
    exit_code = await runner.run("bad_agent", "", tmp_path, on_event=events.append)
    # Should either emit STARTUP_FAILED or return non-zero exit
    assert exit_code != 0 or any(e.event == RunnerEventType.STARTUP_FAILED for e in events)


@pytest.mark.asyncio
async def test_runner_does_not_require_bash_on_path(tmp_path, monkeypatch):
    events = []
    monkeypatch.setenv("PATH", "/usr/local/bin")
    cfg = make_cfg({"echo_agent": "printf ok"})
    runner = CLIRunner(cfg)
    exit_code = await runner.run("echo_agent", "", tmp_path, on_event=events.append)
    assert exit_code == 0
    event_types = [e.event for e in events]
    assert RunnerEventType.SESSION_STARTED in event_types
    assert RunnerEventType.TURN_COMPLETED in event_types


@pytest.mark.asyncio
async def test_get_command_returns_configured(tmp_path):
    cfg = make_cfg({"my_agent": "echo test"})
    runner = CLIRunner(cfg)
    assert runner.get_command("my_agent") == "echo test"


@pytest.mark.asyncio
async def test_session_started_has_pid(tmp_path):
    events = []
    cfg = make_cfg({"echo_agent": "echo hi"})
    runner = CLIRunner(cfg)
    await runner.run("echo_agent", "", tmp_path, on_event=events.append)
    started = [e for e in events if e.event == RunnerEventType.SESSION_STARTED]
    assert len(started) == 1
    assert started[0].pid is not None


@pytest.mark.asyncio
async def test_exit_code_is_returned_on_success(tmp_path):
    events = []
    cfg = make_cfg({"echo_agent": "echo done"})
    runner = CLIRunner(cfg)
    exit_code = await runner.run("echo_agent", "", tmp_path, on_event=events.append)
    assert exit_code == 0


@pytest.mark.asyncio
async def test_exit_code_is_nonzero_on_failure(tmp_path):
    events = []
    cfg = make_cfg({"fail_agent": "exit 42"})
    runner = CLIRunner(cfg)
    exit_code = await runner.run("fail_agent", "", tmp_path, on_event=events.append)
    assert exit_code == 42


@pytest.mark.asyncio
async def test_multiple_output_lines_all_streamed(tmp_path):
    events = []
    cfg = make_cfg({"multi_agent": "printf 'line1\\nline2\\nline3\\n'"})
    runner = CLIRunner(cfg)
    await runner.run("multi_agent", "", tmp_path, on_event=events.append)
    streaming = [e.message for e in events if e.event == RunnerEventType.STREAMING]
    combined = " ".join(streaming)
    assert "line1" in combined
    assert "line2" in combined
    assert "line3" in combined


@pytest.mark.asyncio
async def test_turn_failed_includes_exit_code(tmp_path):
    events = []
    cfg = make_cfg({"fail_agent": "exit 7"})
    runner = CLIRunner(cfg)
    await runner.run("fail_agent", "", tmp_path, on_event=events.append)
    failed = [e for e in events if e.event == RunnerEventType.TURN_FAILED]
    assert len(failed) == 1
    assert failed[0].exit_code == 7


@pytest.mark.asyncio
async def test_timeout_returns_negative_exit_code(tmp_path):
    events = []
    cfg = make_cfg({"slow_agent": "sleep 60"}, timeout_ms=150)
    runner = CLIRunner(cfg)
    exit_code = await runner.run("slow_agent", "", tmp_path, on_event=events.append)
    assert exit_code < 0
