"""Generic CLI runner — Orbit §3.4 MVP Runner."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import RunnerConfig
from ..models import Issue
from .base import RunnerEvent, RunnerEventType

log = logging.getLogger(__name__)


def _shell_path() -> str:
    """Return a shell executable without depending on PATH lookup."""
    for candidate in ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return "sh"


class CLIRunner:
    """Invokes the selected agent command with the rendered prompt on stdin.

    Orbit §3.4 generic CLI runner contract:
    1. Select agent command from runner.commands[agent_name].
    2. Pass rendered prompt via stdin.
    3. Set cwd = workspace_path.
    4. Emit events; detect completion via tracker handoff (caller's responsibility).
    5. Enforce timeout and stall timeout.
    """

    def __init__(self, cfg: RunnerConfig):
        self._cfg = cfg

    def get_command(self, agent_name: str) -> str:
        cmd = self._cfg.commands.get(agent_name)
        if not cmd:
            raise ValueError(
                f"runner_command_not_found: no command configured for agent {agent_name!r}"
            )
        return cmd

    async def run(
        self,
        agent_name: str,
        prompt: str,
        workspace_path: Path,
        on_event: Callable[[RunnerEvent], None],
    ) -> int:
        """Run the agent command; return exit code.

        Emits RunnerEvent objects via on_event callback.
        """
        command = self.get_command(agent_name)
        timeout_s = self._cfg.timeout_ms / 1000.0
        stall_s = self._cfg.stall_timeout_ms / 1000.0

        log.info(
            "runner_launch agent=%s command=%s workspace=%s",
            agent_name, command, workspace_path,
        )

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                _shell_path(), "-c", command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace_path),
            )
        except Exception as exc:
            log.exception(
                "runner_startup_failed agent=%s command=%s workspace=%s error=%r",
                agent_name, command, workspace_path, exc,
            )
            on_event(RunnerEvent(
                event=RunnerEventType.STARTUP_FAILED,
                message=f"{type(exc).__name__}: {exc}",
            ))
            log.info(
                "runner_exit agent=%s exit_code=%d",
                agent_name, -1,
            )
            return -1

        on_event(RunnerEvent(
            event=RunnerEventType.SESSION_STARTED,
            pid=proc.pid,
            message=f"agent={agent_name} pid={proc.pid}",
        ))

        # Write prompt to stdin, then close
        prompt_bytes = prompt.encode("utf-8")
        if proc.stdin:
            try:
                proc.stdin.write(prompt_bytes)
                await proc.stdin.drain()
                proc.stdin.close()
                await proc.stdin.wait_closed()
            except Exception:
                pass

        last_event_at = datetime.now(timezone.utc)

        async def _read_stream(stream: asyncio.StreamReader, tag: str) -> None:
            nonlocal last_event_at
            while True:
                try:
                    line_bytes = await asyncio.wait_for(stream.readline(), timeout=stall_s)
                except asyncio.TimeoutError:
                    on_event(RunnerEvent(
                        event=RunnerEventType.STALLED,
                        pid=proc.pid if proc else None,
                        message=f"no output for {stall_s:.0f}s",
                    ))
                    if proc:
                        proc.kill()
                    return
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").rstrip()
                last_event_at = datetime.now(timezone.utc)
                on_event(RunnerEvent(
                    event=RunnerEventType.STREAMING,
                    pid=proc.pid if proc else None,
                    message=f"[{tag}] {line}",
                ))

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stream(proc.stdout, "stdout"),  # type: ignore[arg-type]
                    _read_stream(proc.stderr, "stderr"),  # type: ignore[arg-type]
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            on_event(RunnerEvent(
                event=RunnerEventType.TURN_TIMEOUT,
                pid=proc.pid,
                message=f"timeout after {timeout_s:.0f}s",
            ))
            proc.kill()
            await proc.communicate()
            return -2

        exit_code = await proc.wait()
        if exit_code == 0:
            on_event(RunnerEvent(
                event=RunnerEventType.TURN_COMPLETED,
                pid=proc.pid,
                exit_code=exit_code,
                message="agent exited successfully",
            ))
        else:
            on_event(RunnerEvent(
                event=RunnerEventType.TURN_FAILED,
                pid=proc.pid,
                exit_code=exit_code,
                message=f"agent exited with code {exit_code}",
            ))

        log.info(
            "runner_exit agent=%s exit_code=%d",
            agent_name, exit_code,
        )
        return exit_code
