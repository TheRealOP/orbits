"""Strict Jinja2 prompt rendering — Symphony §5.4 + Orbit §7."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError

from .models import Issue


def _issue_to_dict(issue: Issue) -> dict[str, Any]:
    d = asdict(issue)
    # Convert datetime objects to ISO strings for template use
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if val is not None:
            d[key] = val.isoformat()
    return d


def render_prompt(
    template_str: str,
    issue: Issue,
    attempt: int | None = None,
    *,
    agent: str = "",
    context: str = "",
    graph_nodes: list[str] | None = None,
    workspace_path: str = "",
) -> str:
    """Render the workflow prompt template with strict variable checking.

    Raises ValueError on template parse or render errors.
    """
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    try:
        tmpl = env.from_string(template_str)
    except TemplateSyntaxError as exc:
        raise ValueError(f"template_parse_error: {exc}") from exc

    variables: dict[str, Any] = {
        "issue": _issue_to_dict(issue),
        "attempt": attempt,
        "agent": agent,
        "context": context,
        "graph_nodes": graph_nodes or [],
        "workspace_path": workspace_path,
    }

    try:
        return tmpl.render(**variables)
    except UndefinedError as exc:
        raise ValueError(f"template_render_error: {exc}") from exc


def render_runner_command(
    command_template: str,
    agent: str,
    workspace_path: str,
    issue: Issue,
    attempt: int | None,
) -> str:
    """Render a runner command template (§3.4)."""
    env = Environment(undefined=StrictUndefined)
    try:
        tmpl = env.from_string(command_template)
    except TemplateSyntaxError as exc:
        raise ValueError(f"template_parse_error in runner command: {exc}") from exc

    try:
        return tmpl.render(
            agent=agent,
            workspace_path=workspace_path,
            issue=_issue_to_dict(issue),
            attempt=attempt,
        )
    except UndefinedError as exc:
        raise ValueError(f"template_render_error in runner command: {exc}") from exc
