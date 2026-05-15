"""Workspace management and safety invariants — Symphony §9."""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .hooks import run_hook, run_hook_best_effort

log = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_key(identifier: str) -> str:
    """Replace unsafe characters with '_' — Symphony §4.2, §9.5 Invariant 3."""
    return _SAFE_RE.sub("_", identifier)


def _assert_within_root(workspace_path: Path, workspace_root: Path) -> None:
    """Enforce Symphony §9.5 Invariant 2: workspace must stay inside root."""
    try:
        workspace_path.relative_to(workspace_root)
    except ValueError:
        raise ValueError(
            f"invalid_workspace_cwd: {workspace_path} is outside workspace root {workspace_root}"
        )


@dataclass
class Workspace:
    path: Path
    workspace_key: str
    created_now: bool


class WorkspaceManager:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    async def create_for_issue(
        self,
        identifier: str,
        *,
        after_create: str | None = None,
        hook_timeout_ms: int = 60_000,
    ) -> Workspace:
        key = sanitize_key(identifier)
        ws_path = (self.root / key).resolve()
        _assert_within_root(ws_path, self.root)

        created_now = False
        if not ws_path.exists():
            ws_path.mkdir(parents=True, exist_ok=True)
            created_now = True
            log.info("workspace_created path=%s", ws_path)
        elif not ws_path.is_dir():
            # Non-directory at expected location — fail safely
            raise RuntimeError(f"workspace_path_not_directory: {ws_path}")
        else:
            log.info("workspace_reused path=%s", ws_path)

        if created_now and after_create:
            try:
                await run_hook("after_create", after_create, ws_path, hook_timeout_ms)
            except Exception as exc:
                # after_create failure is fatal — remove the partially prepared dir
                shutil.rmtree(ws_path, ignore_errors=True)
                raise RuntimeError(f"workspace_creation_failed: {exc}") from exc

        return Workspace(path=ws_path, workspace_key=key, created_now=created_now)

    async def remove_for_issue(
        self,
        identifier: str,
        *,
        before_remove: str | None = None,
        hook_timeout_ms: int = 60_000,
    ) -> None:
        key = sanitize_key(identifier)
        ws_path = (self.root / key).resolve()
        _assert_within_root(ws_path, self.root)

        if not ws_path.exists():
            return

        await run_hook_best_effort("before_remove", before_remove, ws_path, hook_timeout_ms)

        try:
            shutil.rmtree(ws_path)
            log.info("workspace_removed path=%s", ws_path)
        except OSError as exc:
            log.warning("workspace_remove_failed path=%s error=%s", ws_path, exc)

    def path_for(self, identifier: str) -> Path:
        key = sanitize_key(identifier)
        ws_path = (self.root / key).resolve()
        _assert_within_root(ws_path, self.root)
        return ws_path

    def assert_safe_cwd(self, workspace_path: Path) -> None:
        """Symphony §9.5 Invariant 1: assert cwd is the per-issue workspace path."""
        workspace_path = workspace_path.resolve()
        _assert_within_root(workspace_path, self.root)
