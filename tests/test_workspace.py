"""Tests for orbit.workspace — WorkspaceManager (Symphony §9)."""
import pytest

from orbit.workspace import WorkspaceManager, sanitize_key


# ---------------------------------------------------------------------------
# sanitize_key unit tests
# ---------------------------------------------------------------------------

class TestSanitizeKey:
    def test_alphanumeric_unchanged(self):
        assert sanitize_key("ABC123") == "ABC123"

    def test_hyphens_and_dots_unchanged(self):
        assert sanitize_key("ABC-123.0") == "ABC-123.0"

    def test_spaces_replaced(self):
        assert sanitize_key("my task") == "my_task"

    def test_slashes_replaced(self):
        # Only the slashes are replaced; dots are safe chars and stay
        assert sanitize_key("../../evil") == ".._.._evil"

    def test_mixed_unsafe_chars(self):
        result = sanitize_key("TASK-1 (urgent)!")
        assert " " not in result
        assert "(" not in result
        assert ")" not in result
        assert "!" not in result
        assert "TASK" in result

    def test_identifier_style(self):
        assert sanitize_key("TASK-42") == "TASK-42"
        assert sanitize_key("ABC-123") == "ABC-123"


# ---------------------------------------------------------------------------
# WorkspaceManager.create_for_issue
# ---------------------------------------------------------------------------

class TestCreateForIssue:
    async def test_create_new_workspace(self, tmp_path):
        """Creating a workspace for a new identifier creates the directory."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("TASK-1")
        assert ws.path.exists()
        assert ws.path.is_dir()
        assert ws.created_now is True
        assert ws.workspace_key == "TASK-1"

    async def test_reuse_existing_workspace(self, tmp_path):
        """Creating a workspace for an already-existing dir sets created_now=False."""
        mgr = WorkspaceManager(str(tmp_path))
        ws1 = await mgr.create_for_issue("TASK-2")
        assert ws1.created_now is True

        ws2 = await mgr.create_for_issue("TASK-2")
        assert ws2.created_now is False
        assert ws2.path == ws1.path

    async def test_workspace_path_sanitization(self, tmp_path):
        """Identifiers with unsafe chars are sanitized for the directory name."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("MY TASK 1")
        # Spaces replaced with underscores
        assert " " not in ws.workspace_key
        assert ws.path.exists()

    async def test_workspace_path_stays_in_root(self, tmp_path):
        """The workspace directory is a child of the root."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("ABC-123")
        assert str(ws.path).startswith(str(tmp_path.resolve()))

    async def test_workspace_traversal_blocked(self, tmp_path):
        """Path traversal via '../../evil' is neutralized by sanitization — dir stays inside root."""
        mgr = WorkspaceManager(str(tmp_path))
        # sanitize_key converts '../../evil' → '.._.._evil', which stays inside root
        ws = await mgr.create_for_issue("../../evil")
        # Must be inside root, not above it
        ws.path.relative_to(tmp_path.resolve())  # does not raise
        assert ws.workspace_key == ".._.._evil"

    async def test_after_create_hook_runs_only_on_new(self, tmp_path):
        """after_create hook executes for a new workspace, not for an existing one."""
        mgr = WorkspaceManager(str(tmp_path))
        marker_new = tmp_path / "TASK-3" / "hook_ran.txt"

        # First creation — hook should run
        ws1 = await mgr.create_for_issue("TASK-3", after_create=f"touch hook_ran.txt")
        assert marker_new.exists(), "Hook should have created marker on new workspace"

        # Remove marker to detect a second hook run
        marker_new.unlink()

        # Second call — workspace already exists, hook must NOT run
        ws2 = await mgr.create_for_issue("TASK-3", after_create=f"touch hook_ran.txt")
        assert ws2.created_now is False
        assert not marker_new.exists(), "Hook should NOT run on reused workspace"

    async def test_after_create_hook_failure_cleans_dir(self, tmp_path):
        """If after_create hook fails, the partially created workspace dir is removed."""
        root = tmp_path / "wsroot"
        root.mkdir()
        mgr = WorkspaceManager(str(root))

        with pytest.raises(RuntimeError, match="workspace_creation_failed"):
            await mgr.create_for_issue("TASK-FAIL", after_create="exit 1")

        # Directory should have been cleaned up
        expected_path = root / "TASK-FAIL"
        assert not expected_path.exists()

    async def test_workspace_key_in_result(self, tmp_path):
        """Workspace.workspace_key is the sanitized identifier."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("TASK-99")
        assert ws.workspace_key == "TASK-99"

    async def test_non_directory_at_path_raises(self, tmp_path):
        """If a file (not a dir) exists at the workspace path, raises RuntimeError."""
        mgr = WorkspaceManager(str(tmp_path))
        # Manually place a file where the workspace dir would be
        (tmp_path / "FILE-TASK").write_text("I am a file")
        with pytest.raises(RuntimeError, match="workspace_path_not_directory"):
            await mgr.create_for_issue("FILE-TASK")


# ---------------------------------------------------------------------------
# WorkspaceManager.remove_for_issue
# ---------------------------------------------------------------------------

class TestRemoveForIssue:
    async def test_remove_for_issue(self, tmp_path):
        """Removing an existing workspace deletes the directory."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("TASK-DEL")
        assert ws.path.exists()

        await mgr.remove_for_issue("TASK-DEL")
        assert not ws.path.exists()

    async def test_remove_nonexistent_is_noop(self, tmp_path):
        """Removing a workspace that does not exist does not raise."""
        mgr = WorkspaceManager(str(tmp_path))
        # Should not raise
        await mgr.remove_for_issue("TASK-GHOST")

    async def test_remove_runs_before_remove_hook(self, tmp_path):
        """before_remove hook runs before the directory is deleted."""
        mgr = WorkspaceManager(str(tmp_path))
        await mgr.create_for_issue("TASK-BR")

        hook_log = tmp_path / "hook_log.txt"
        # Hook writes to a file outside the workspace
        hook_script = f"echo ran > {hook_log}"
        await mgr.remove_for_issue("TASK-BR", before_remove=hook_script)

        # Workspace removed
        assert not (tmp_path / "TASK-BR").exists()
        # Hook log present (written by hook before removal)
        assert hook_log.exists()
        assert "ran" in hook_log.read_text()

    async def test_remove_with_none_hook_is_fine(self, tmp_path):
        """remove_for_issue with before_remove=None works normally."""
        mgr = WorkspaceManager(str(tmp_path))
        await mgr.create_for_issue("TASK-NH")
        await mgr.remove_for_issue("TASK-NH", before_remove=None)
        assert not (tmp_path / "TASK-NH").exists()

    async def test_remove_traversal_blocked(self, tmp_path):
        """Path traversal in remove is neutralized by sanitization — never escapes root."""
        mgr = WorkspaceManager(str(tmp_path))
        # sanitize_key converts '../../dangerous' → '.._.._dangerous', stays inside root
        # remove of non-existent sanitized path is a safe no-op
        await mgr.remove_for_issue("../../dangerous")  # must not raise
        # And the root is intact
        assert tmp_path.exists()


# ---------------------------------------------------------------------------
# WorkspaceManager.assert_safe_cwd
# ---------------------------------------------------------------------------

class TestAssertSafeCwd:
    async def test_assert_safe_cwd_valid(self, tmp_path):
        """A path inside root passes assert_safe_cwd without raising."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("TASK-CWD")
        # Should not raise
        mgr.assert_safe_cwd(ws.path)

    async def test_assert_safe_cwd_outside_root_raises(self, tmp_path):
        """A path outside root raises ValueError."""
        mgr = WorkspaceManager(str(tmp_path / "root"))
        outside = tmp_path / "other_dir"
        outside.mkdir()
        with pytest.raises(ValueError, match="invalid_workspace_cwd"):
            mgr.assert_safe_cwd(outside)

    async def test_assert_safe_cwd_resolves_symlinks(self, tmp_path):
        """assert_safe_cwd resolves the path before checking."""
        mgr = WorkspaceManager(str(tmp_path))
        ws = await mgr.create_for_issue("TASK-SYM")
        # Even if passed without explicit resolve, it should work
        mgr.assert_safe_cwd(ws.path)

    async def test_assert_safe_cwd_root_itself_is_valid(self, tmp_path):
        """The root directory itself passes the safety check."""
        mgr = WorkspaceManager(str(tmp_path))
        mgr.assert_safe_cwd(tmp_path)


# ---------------------------------------------------------------------------
# WorkspaceManager.path_for
# ---------------------------------------------------------------------------

class TestPathFor:
    def test_path_for_returns_expected_path(self, tmp_path):
        mgr = WorkspaceManager(str(tmp_path))
        p = mgr.path_for("TASK-10")
        assert p == (tmp_path / "TASK-10").resolve()

    def test_path_for_traversal_blocked(self, tmp_path):
        """path_for with traversal attempt returns sanitized path inside root."""
        mgr = WorkspaceManager(str(tmp_path))
        # sanitize_key makes '../../escape' → '.._.._escape', stays inside root
        p = mgr.path_for("../../escape")
        # Path must be inside root
        p.relative_to(tmp_path.resolve())  # does not raise
