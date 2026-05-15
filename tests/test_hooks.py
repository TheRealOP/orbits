"""Tests for orbit.hooks — async hook runner (Symphony §9.4)."""
import logging

import pytest

from orbit.hooks import run_hook, run_hook_best_effort


# ---------------------------------------------------------------------------
# run_hook tests
# ---------------------------------------------------------------------------

class TestRunHook:
    async def test_run_hook_none_script_is_noop(self, tmp_path):
        """run_hook with None script returns immediately without error."""
        # Should not raise and should not create any side effects
        await run_hook("noop", None, tmp_path, timeout_ms=5000)

    async def test_run_hook_empty_script_is_noop(self, tmp_path):
        """run_hook with empty string script is a no-op."""
        await run_hook("noop_empty", "", tmp_path, timeout_ms=5000)

    async def test_run_hook_success(self, tmp_path):
        """A hook script that exits 0 completes without raising."""
        await run_hook("hello", "echo hello", tmp_path, timeout_ms=5000)

    async def test_run_hook_creates_file(self, tmp_path):
        """Hook script side effects are visible after completion."""
        marker = tmp_path / "hook_ran.txt"
        script = f"touch {marker}"
        await run_hook("create_marker", script, tmp_path, timeout_ms=5000)
        assert marker.exists()

    async def test_run_hook_failure_raises(self, tmp_path):
        """A script that exits non-zero raises RuntimeError."""
        with pytest.raises(RuntimeError, match="hook_failed"):
            await run_hook("fail", "exit 1", tmp_path, timeout_ms=5000)

    async def test_run_hook_failure_contains_exit_code(self, tmp_path):
        """RuntimeError message includes exit code."""
        with pytest.raises(RuntimeError, match="exit="):
            await run_hook("fail42", "exit 42", tmp_path, timeout_ms=5000)

    async def test_run_hook_timeout_raises(self, tmp_path):
        """A script that runs too long raises RuntimeError with timeout info."""
        with pytest.raises(RuntimeError, match="hook_timeout"):
            await run_hook("slow", "sleep 10", tmp_path, timeout_ms=100)

    async def test_run_hook_timeout_message_includes_name(self, tmp_path):
        """Timeout error message includes the hook name."""
        with pytest.raises(RuntimeError, match="slow_hook"):
            await run_hook("slow_hook", "sleep 10", tmp_path, timeout_ms=100)

    async def test_run_hook_cwd_is_passed(self, tmp_path):
        """Hook runs in the given cwd — pwd output matches tmp_path."""
        marker = tmp_path / "cwd_check.txt"
        script = f"pwd > {marker}"
        await run_hook("cwd_check", script, tmp_path, timeout_ms=5000)
        content = marker.read_text().strip()
        # The resolved real path should match
        assert content == str(tmp_path.resolve())

    async def test_run_hook_stderr_captured_in_error(self, tmp_path):
        """Hook failure includes stderr in the RuntimeError message."""
        with pytest.raises(RuntimeError) as exc_info:
            await run_hook("err_output", "echo 'oops' >&2; exit 1", tmp_path, timeout_ms=5000)
        assert "oops" in str(exc_info.value)

    async def test_run_hook_multiline_script(self, tmp_path):
        """Multi-statement script (semicolons) runs correctly."""
        marker = tmp_path / "multi.txt"
        script = f"echo first; echo second > {marker}"
        await run_hook("multi", script, tmp_path, timeout_ms=5000)
        assert marker.exists()
        assert "second" in marker.read_text()


# ---------------------------------------------------------------------------
# run_hook_best_effort tests
# ---------------------------------------------------------------------------

class TestRunHookBestEffort:
    async def test_run_hook_best_effort_logs_and_doesnt_raise(self, tmp_path, caplog):
        """Failing hook with best_effort logs a warning but does NOT raise."""
        with caplog.at_level(logging.WARNING, logger="orbit.hooks"):
            await run_hook_best_effort("bad", "exit 1", tmp_path, timeout_ms=5000)
        # Should have logged a warning
        assert any("hook_ignored" in r.message or "bad" in r.message for r in caplog.records)

    async def test_run_hook_best_effort_success_is_silent(self, tmp_path, caplog):
        """Successful best-effort hook does not log warnings."""
        with caplog.at_level(logging.WARNING, logger="orbit.hooks"):
            await run_hook_best_effort("ok", "echo ok", tmp_path, timeout_ms=5000)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []

    async def test_run_hook_best_effort_none_is_noop(self, tmp_path):
        """None script with best_effort does nothing."""
        await run_hook_best_effort("noop", None, tmp_path, timeout_ms=5000)

    async def test_run_hook_best_effort_timeout_doesnt_raise(self, tmp_path, caplog):
        """Timeout in best_effort is swallowed — no exception propagated."""
        with caplog.at_level(logging.WARNING, logger="orbit.hooks"):
            await run_hook_best_effort("timeout_be", "sleep 10", tmp_path, timeout_ms=100)
        # Just verify no exception raised; warning should be present
        assert any("timeout_be" in r.message or "hook_ignored" in r.message for r in caplog.records)

    async def test_run_hook_best_effort_creates_file_on_success(self, tmp_path):
        """Even best-effort hooks execute their side effects on success."""
        marker = tmp_path / "be_marker.txt"
        await run_hook_best_effort("be_ok", f"touch {marker}", tmp_path, timeout_ms=5000)
        assert marker.exists()
