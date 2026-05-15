"""Core domain models for Orbit — extends Symphony §4 with Orbit §5."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    PREPARING_WORKSPACE = "preparing_workspace"
    BUILDING_PROMPT = "building_prompt"
    LAUNCHING_AGENT = "launching_agent"
    INITIALIZING_SESSION = "initializing_session"
    STREAMING_TURN = "streaming_turn"
    FINISHING = "finishing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    STALLED = "stalled"
    CANCELED_BY_RECONCILIATION = "canceled_by_reconciliation"


class AgentMode(str, Enum):
    CLI = "cli"
    APP_SERVER = "app_server"
    STREAMING_CLI = "streaming_cli"
    PASSTHROUGH = "passthrough"


@dataclass
class BlockerRef:
    id: str | None = None
    identifier: str | None = None
    state: str | None = None


@dataclass
class Issue:
    id: str
    identifier: str
    title: str
    description: str | None = None
    priority: int | None = None
    state: str = ""
    branch_name: str | None = None
    url: str | None = None
    labels: list[str] = field(default_factory=list)
    blocked_by: list[BlockerRef] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Orbit extensions (§5.1)
    selected_agent: str | None = None
    knowledge_context: str | None = None
    graph_nodes: list[str] = field(default_factory=list)


@dataclass
class RunAttempt:
    issue_id: str
    issue_identifier: str
    attempt: int | None
    workspace_path: str
    started_at: datetime
    status: RunStatus = RunStatus.PREPARING_WORKSPACE
    error: str | None = None
    # Orbit extensions (§5.2)
    agent_name: str = ""
    agent_mode: AgentMode = AgentMode.CLI
    knowledge_injected: bool = False
    knowledge_ingested: bool = False


@dataclass
class LiveSession:
    session_id: str
    thread_id: str
    turn_id: str
    pid: int | None = None
    last_event: str | None = None
    last_event_at: datetime | None = None
    last_message: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    turn_count: int = 0


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at: datetime
    error: str | None = None


@dataclass
class RunningEntry:
    issue: Issue
    attempt: int | None
    workspace_path: str
    started_at: datetime
    agent_name: str = ""
    session: LiveSession | None = None


@dataclass
class AgentTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    seconds_running: float = 0.0


@dataclass
class OrchestratorState:
    poll_interval_ms: int
    max_concurrent_agents: int
    running: dict[str, RunningEntry] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    retry_attempts: dict[str, RetryEntry] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    agent_totals: AgentTotals = field(default_factory=AgentTotals)
    rate_limits: Any | None = None
    # Orbit extensions (§5.3)
    agent_running_counts: dict[str, int] = field(default_factory=dict)
    knowledge_available: bool = False
