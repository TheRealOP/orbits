"""Workflow config loader and typed config layer.

Implements Symphony §5 (WORKFLOW.md format) + Orbit §6 (schema extensions).
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_VAR_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


def _resolve_var(value: str) -> str:
    """Expand $VAR_NAME references; return value unchanged if not a plain $VAR."""
    m = _VAR_RE.match(value.strip())
    if m:
        return os.environ.get(m.group(1), "")
    return value


def _expand_path(value: str, workflow_dir: Path) -> str:
    """Expand ~ and $VAR, then resolve relative paths against workflow_dir."""
    if _VAR_RE.match(value.strip()):
        value = _resolve_var(value)
        if not value:
            return value
    value = os.path.expanduser(value)
    p = Path(value)
    if not p.is_absolute():
        p = workflow_dir / p
    return str(p.resolve())


# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TrackerConfig:
    kind: str = ""
    endpoint: str = ""
    api_key: str = ""
    project_slug: str = ""
    active_states: list[str] = field(default_factory=lambda: ["Todo", "In Progress"])
    terminal_states: list[str] = field(
        default_factory=lambda: ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"]
    )
    # GitHub-specific
    repo: str = ""
    active_labels: list[str] = field(default_factory=lambda: ["orbit:todo", "orbit:in-progress"])
    running_label: str = "orbit:in-progress"
    terminal_labels: list[str] = field(default_factory=lambda: ["orbit:done", "orbit:cancelled"])
    handoff_label: str = "orbit:review"
    poll_interval_ms: int = 60000
    # File-specific
    path: str = ""


@dataclass
class PollingConfig:
    interval_ms: int = 30000


@dataclass
class WorkspaceConfig:
    root: str = ""


@dataclass
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = 60000


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 10
    max_turns: int = 20
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)
    # Orbit extensions (§3.6)
    max_concurrent_by_agent: dict[str, int] = field(default_factory=dict)


@dataclass
class CodexConfig:
    command: str = "codex app-server"
    approval_policy: str = "never"
    thread_sandbox: str = "workspace-write"
    turn_sandbox_policy: str = ""
    turn_timeout_ms: int = 3_600_000
    read_timeout_ms: int = 5_000
    stall_timeout_ms: int = 300_000


@dataclass
class AgentEntry:
    capabilities: list[str] = field(default_factory=list)
    weight: int = 5


@dataclass
class AgentsConfig:
    default: str = "codex"
    registry: dict[str, AgentEntry] = field(default_factory=dict)


@dataclass
class RoutingConfig:
    command: str | None = None
    timeout_ms: int = 5_000
    fallback: str = "codex"


@dataclass
class RunnerConfig:
    kind: str = "cli"
    commands: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000


@dataclass
class KnowledgeConfig:
    enabled: bool = False
    akms_root: str = ""
    config_path: str = ""
    context_sections: list[str] = field(default_factory=list)
    inject_context: bool = True
    ingest_results: bool = True
    context_max_tokens: int = 2_000
    ingest_on_states: list[str] = field(default_factory=lambda: ["Human Review", "orbit:review"])


@dataclass
class ServerConfig:
    port: int | None = None


@dataclass
class WorkflowDefinition:
    config: dict[str, Any]
    prompt_template: str
    path: Path


@dataclass
class ServiceConfig:
    tracker: TrackerConfig
    polling: PollingConfig
    workspace: WorkspaceConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    agents: AgentsConfig
    routing: RoutingConfig
    runner: RunnerConfig
    knowledge: KnowledgeConfig
    server: ServerConfig
    workflow_dir: Path


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_tracker(raw: dict, workflow_dir: Path) -> TrackerConfig:
    cfg = TrackerConfig()
    cfg.kind = raw.get("kind", "")
    cfg.endpoint = raw.get("endpoint", "https://api.linear.app/graphql")
    api_key = raw.get("api_key", "$LINEAR_API_KEY")
    cfg.api_key = _resolve_var(str(api_key)) if isinstance(api_key, str) else str(api_key)
    cfg.project_slug = raw.get("project_slug", "")
    cfg.active_states = raw.get("active_states", ["Todo", "In Progress"])
    cfg.terminal_states = raw.get(
        "terminal_states", ["Closed", "Cancelled", "Canceled", "Duplicate", "Done"]
    )
    cfg.repo = raw.get("repo", "")
    cfg.active_labels = raw.get("active_labels", ["orbit:todo", "orbit:in-progress"])
    cfg.running_label = raw.get("running_label", "orbit:in-progress")
    cfg.terminal_labels = raw.get("terminal_labels", ["orbit:done", "orbit:cancelled"])
    cfg.handoff_label = raw.get("handoff_label", "orbit:review")
    cfg.poll_interval_ms = int(raw.get("poll_interval_ms", 60000))
    path_val = raw.get("path", "")
    cfg.path = _expand_path(path_val, workflow_dir) if path_val else ""
    return cfg


def _parse_workspace(raw: dict, workflow_dir: Path) -> WorkspaceConfig:
    cfg = WorkspaceConfig()
    root = raw.get("root", "")
    if root:
        cfg.root = _expand_path(str(root), workflow_dir)
    else:
        cfg.root = str(Path(tempfile.gettempdir()) / "orbit_workspaces")
    return cfg


def _parse_hooks(raw: dict) -> HooksConfig:
    cfg = HooksConfig()
    cfg.after_create = raw.get("after_create")
    cfg.before_run = raw.get("before_run")
    cfg.after_run = raw.get("after_run")
    cfg.before_remove = raw.get("before_remove")
    timeout = raw.get("timeout_ms", 60000)
    try:
        timeout = int(timeout)
        if timeout <= 0:
            raise ValueError
        cfg.timeout_ms = timeout
    except (TypeError, ValueError):
        raise ValueError(f"hooks.timeout_ms must be a positive integer, got {timeout!r}")
    return cfg


def _parse_agent(raw: dict) -> AgentConfig:
    cfg = AgentConfig()
    cfg.max_concurrent_agents = int(raw.get("max_concurrent_agents", 10))
    max_turns = int(raw.get("max_turns", 20))
    if max_turns <= 0:
        raise ValueError(f"agent.max_turns must be positive, got {max_turns}")
    cfg.max_turns = max_turns
    cfg.max_retry_backoff_ms = int(raw.get("max_retry_backoff_ms", 300_000))
    by_state: dict[str, int] = {}
    for k, v in raw.get("max_concurrent_agents_by_state", {}).items():
        try:
            n = int(v)
            if n > 0:
                by_state[k.lower()] = n
        except (TypeError, ValueError):
            pass
    cfg.max_concurrent_agents_by_state = by_state
    by_agent: dict[str, int] = {}
    for k, v in raw.get("max_concurrent_by_agent", {}).items():
        try:
            n = int(v)
            if n > 0:
                by_agent[k] = n
        except (TypeError, ValueError):
            pass
    cfg.max_concurrent_by_agent = by_agent
    return cfg


def _parse_codex(raw: dict) -> CodexConfig:
    cfg = CodexConfig()
    cfg.command = raw.get("command", "codex app-server")
    cfg.approval_policy = raw.get("approval_policy", "never")
    cfg.thread_sandbox = raw.get("thread_sandbox", "workspace-write")
    cfg.turn_sandbox_policy = raw.get("turn_sandbox_policy", "")
    cfg.turn_timeout_ms = int(raw.get("turn_timeout_ms", 3_600_000))
    cfg.read_timeout_ms = int(raw.get("read_timeout_ms", 5_000))
    cfg.stall_timeout_ms = int(raw.get("stall_timeout_ms", 300_000))
    return cfg


def _parse_agents(raw: dict) -> AgentsConfig:
    cfg = AgentsConfig()
    cfg.default = raw.get("default", "codex")
    registry: dict[str, AgentEntry] = {}
    for name, entry_raw in raw.get("registry", {}).items():
        entry = AgentEntry()
        entry.capabilities = [c.lower() for c in entry_raw.get("capabilities", [])]
        entry.weight = int(entry_raw.get("weight", 5))
        registry[name] = entry
    cfg.registry = registry
    return cfg


def _parse_routing(raw: dict) -> RoutingConfig:
    cfg = RoutingConfig()
    cfg.command = raw.get("command") or None
    cfg.timeout_ms = int(raw.get("timeout_ms", 5_000))
    cfg.fallback = raw.get("fallback", "codex")
    return cfg


def _parse_runner(raw: dict) -> RunnerConfig:
    cfg = RunnerConfig()
    cfg.kind = raw.get("kind", "cli")
    cfg.commands = {k: str(v) for k, v in raw.get("commands", {}).items()}
    cfg.timeout_ms = int(raw.get("timeout_ms", 3_600_000))
    cfg.stall_timeout_ms = int(raw.get("stall_timeout_ms", 300_000))
    return cfg


def _parse_knowledge(raw: dict) -> KnowledgeConfig:
    cfg = KnowledgeConfig()
    cfg.enabled = bool(raw.get("enabled", False))
    akms_root = str(raw.get("akms_root", ""))
    cfg.akms_root = _resolve_var(akms_root) if akms_root.startswith("$") else akms_root
    config_path = str(raw.get("config_path", ""))
    if config_path.startswith("$"):
        config_path = _resolve_var(config_path)
    cfg.config_path = config_path
    if not cfg.config_path and cfg.akms_root:
        cfg.config_path = str(Path(cfg.akms_root) / "akms_config.yaml")
    cfg.context_sections = list(raw.get("context_sections", []))
    cfg.inject_context = bool(raw.get("inject_context", True))
    cfg.ingest_results = bool(raw.get("ingest_results", True))
    cfg.context_max_tokens = int(raw.get("context_max_tokens", 2_000))
    cfg.ingest_on_states = list(raw.get("ingest_on_states", ["Human Review", "orbit:review"]))
    return cfg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_workflow(path: Path) -> WorkflowDefinition:
    """Parse WORKFLOW.md / ORBIT.md — YAML front matter + prompt body."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"missing_workflow_file: {exc}") from exc

    prompt_template = text
    raw_config: dict[str, Any] = {}

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            front = parts[1]
            body = parts[2]
            try:
                parsed = yaml.safe_load(front)
            except yaml.YAMLError as exc:
                raise ValueError(f"workflow_parse_error: {exc}") from exc
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict):
                raise ValueError("workflow_front_matter_not_a_map")
            raw_config = parsed
            prompt_template = body.strip()

    return WorkflowDefinition(
        config=raw_config,
        prompt_template=prompt_template,
        path=path,
    )


def build_service_config(workflow: WorkflowDefinition) -> ServiceConfig:
    """Build typed ServiceConfig from a parsed WorkflowDefinition."""
    raw = workflow.config
    workflow_dir = workflow.path.parent

    tracker_raw = raw.get("tracker", {}) or {}
    polling_raw = raw.get("polling", {}) or {}
    workspace_raw = raw.get("workspace", {}) or {}
    hooks_raw = raw.get("hooks", {}) or {}
    agent_raw = raw.get("agent", {}) or {}
    codex_raw = raw.get("codex", {}) or {}
    agents_raw = raw.get("agents", {}) or {}
    routing_raw = raw.get("routing", {}) or {}
    runner_raw = raw.get("runner", {}) or {}
    knowledge_raw = raw.get("knowledge", {}) or {}
    server_raw = raw.get("server", {}) or {}

    tracker = _parse_tracker(tracker_raw, workflow_dir)
    polling = PollingConfig(interval_ms=int(polling_raw.get("interval_ms", 30_000)))
    workspace = _parse_workspace(workspace_raw, workflow_dir)
    hooks = _parse_hooks(hooks_raw)
    agent = _parse_agent(agent_raw)
    codex = _parse_codex(codex_raw)
    agents = _parse_agents(agents_raw)
    routing = _parse_routing(routing_raw)
    runner = _parse_runner(runner_raw)
    knowledge = _parse_knowledge(knowledge_raw)

    server = ServerConfig()
    port = server_raw.get("port")
    server.port = int(port) if port is not None else None

    return ServiceConfig(
        tracker=tracker,
        polling=polling,
        workspace=workspace,
        hooks=hooks,
        agent=agent,
        codex=codex,
        agents=agents,
        routing=routing,
        runner=runner,
        knowledge=knowledge,
        server=server,
        workflow_dir=workflow_dir,
    )


def validate_dispatch_config(cfg: ServiceConfig) -> list[str]:
    """Return list of validation error strings; empty = valid."""
    errors: list[str] = []
    if not cfg.tracker.kind:
        errors.append("tracker.kind is required")
    elif cfg.tracker.kind not in ("linear", "github", "file"):
        errors.append(f"unsupported_tracker_kind: {cfg.tracker.kind!r}")
    if cfg.tracker.kind == "linear":
        if not cfg.tracker.api_key:
            errors.append("missing_tracker_api_key: tracker.api_key is required for linear")
        if not cfg.tracker.project_slug:
            errors.append("missing_tracker_project_slug: tracker.project_slug is required for linear")
    if cfg.tracker.kind == "github":
        if not cfg.tracker.api_key:
            errors.append("missing_tracker_api_key: tracker.api_key (GITHUB_TOKEN) is required for github")
        if not cfg.tracker.repo:
            errors.append("tracker.repo is required for github (e.g. 'owner/repo')")
    if cfg.tracker.kind == "file":
        if not cfg.tracker.path:
            errors.append("tracker.path is required for file tracker")
    return errors
