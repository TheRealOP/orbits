"""Workspace lifecycle hook runner — Symphony §9.4."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


async def run_hook(
    name: str,
    script: str | None,
    cwd: Path,
    timeout_ms: int,
) -> None:
    """Run a shell hook script. Raises RuntimeError on failure."""
    if not script:
        return

    log.info("hook_start name=%s cwd=%s", name, cwd)
    timeout_s = timeout_ms / 1000.0
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-lc", script,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(f"hook_timeout name={name} timeout_ms={timeout_ms}")

        if proc.returncode != 0:
            out_preview = (stdout or b"").decode(errors="replace")[-500:]
            err_preview = (stderr or b"").decode(errors="replace")[-500:]
            raise RuntimeError(
                f"hook_failed name={name} exit={proc.returncode} "
                f"stdout={out_preview!r} stderr={err_preview!r}"
            )
        log.info("hook_ok name=%s", name)

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"hook_error name={name}: {exc}") from exc


async def run_hook_best_effort(
    name: str,
    script: str | None,
    cwd: Path,
    timeout_ms: int,
) -> None:
    """Run a hook but only log failures — used for after_run and before_remove."""
    try:
        await run_hook(name, script, cwd, timeout_ms)
    except Exception as exc:
        log.warning("hook_ignored name=%s error=%s", name, exc)
