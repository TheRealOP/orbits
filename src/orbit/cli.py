"""CLI entry point for Orbit.

Usage:
    orbit [--port PORT] [WORKFLOW_PATH]
    python -m orbit [--port PORT] [WORKFLOW_PATH]
"""
import asyncio
import logging
import sys
from pathlib import Path

import click

from .orchestrator import Orchestrator


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


@click.command()
@click.argument("workflow_path", default="ORBIT.md")
@click.option("--port", "-p", default=None, type=int, help="HTTP server port")
def main(workflow_path: str, port: int | None) -> None:
    """Orbit agent orchestration service."""
    _setup_logging()
    path = Path(workflow_path)
    if not path.exists():
        click.echo(f"error: workflow file not found: {path}", err=True)
        sys.exit(1)

    orchestrator = Orchestrator(path, port=port)
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        pass
    except FileNotFoundError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"startup failed: {exc}", err=True)
        sys.exit(1)
