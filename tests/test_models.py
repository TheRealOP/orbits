"""Tests for orbit.models — dataclasses and enums."""
from datetime import datetime

import pytest

from orbit.models import (
    AgentMode,
    AgentTotals,
    BlockerRef,
    Issue,
    LiveSession,
    OrchestratorState,
    RetryEntry,
    RunAttempt,
    RunningEntry,
    RunStatus,
)


# ---------------------------------------------------------------------------
# RunStatus enum
# ---------------------------------------------------------------------------

class TestRunStatus:
    def test_values_are_strings(self):
        for member in RunStatus:
            assert isinstance(member.value, str)

    def test_expected_string_values(self):
        assert RunStatus.PREPARING_WORKSPACE == "preparing_workspace"
        assert RunStatus.BUILDING_PROMPT == "building_prompt"
        assert RunStatus.LAUNCHING_AGENT == "launching_agent"
        assert RunStatus.INITIALIZING_SESSION == "initializing_session"
        assert RunStatus.STREAMING_TURN == "streaming_turn"
        assert RunStatus.FINISHING == "finishing"
        assert RunStatus.SUCCEEDED == "succeeded"
        assert RunStatus.FAILED == "failed"
        assert RunStatus.TIMED_OUT == "timed_out"
        assert RunStatus.STALLED == "stalled"
        assert RunStatus.CANCELED_BY_RECONCILIATION == "canceled_by_reconciliation"

    def test_is_str_subclass(self):
        # RunStatus extends str — can be used directly as a string
        assert RunStatus.SUCCEEDED == "succeeded"
        assert "succeeded" == RunStatus.SUCCEEDED

    def test_lookup_by_value(self):
        assert RunStatus("failed") is RunStatus.FAILED


# ---------------------------------------------------------------------------
# AgentMode enum
# ---------------------------------------------------------------------------

class TestAgentMode:
    def test_values(self):
        assert AgentMode.CLI == "cli"
        assert AgentMode.APP_SERVER == "app_server"
        assert AgentMode.STREAMING_CLI == "streaming_cli"
        assert AgentMode.PASSTHROUGH == "passthrough"

    def test_is_str_subclass(self):
        assert AgentMode.CLI == "cli"

    def test_all_members_present(self):
        names = {m.name for m in AgentMode}
        assert names == {"CLI", "APP_SERVER", "STREAMING_CLI", "PASSTHROUGH"}


# ---------------------------------------------------------------------------
# BlockerRef dataclass
# ---------------------------------------------------------------------------

class TestBlockerRef:
    def test_all_fields_default_none(self):
        b = BlockerRef()
        assert b.id is None
        assert b.identifier is None
        assert b.state is None

    def test_explicit_fields(self):
        b = BlockerRef(id="99", identifier="TASK-99", state="In Progress")
        assert b.id == "99"
        assert b.identifier == "TASK-99"
        assert b.state == "In Progress"

    def test_partial_construction(self):
        b = BlockerRef(id="42")
        assert b.id == "42"
        assert b.identifier is None


# ---------------------------------------------------------------------------
# Issue dataclass
# ---------------------------------------------------------------------------

class TestIssue:
    def test_minimal_creation(self):
        issue = Issue(id="1", identifier="TASK-1", title="Do something")
        assert issue.id == "1"
        assert issue.identifier == "TASK-1"
        assert issue.title == "Do something"

    def test_defaults(self):
        issue = Issue(id="1", identifier="TASK-1", title="X")
        assert issue.description is None
        assert issue.priority is None
        assert issue.state == ""
        assert issue.branch_name is None
        assert issue.url is None
        assert issue.labels == []
        assert issue.blocked_by == []
        assert issue.created_at is None
        assert issue.updated_at is None
        assert issue.selected_agent is None
        assert issue.knowledge_context is None
        assert issue.graph_nodes == []

    def test_labels_default_independent(self):
        """Each Issue gets its own labels list."""
        a = Issue(id="1", identifier="A-1", title="a")
        b = Issue(id="2", identifier="B-1", title="b")
        a.labels.append("bug")
        assert b.labels == []

    def test_all_fields(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        blocker = BlockerRef(id="99", identifier="TASK-99", state="Todo")
        issue = Issue(
            id="7",
            identifier="TASK-7",
            title="Full issue",
            description="A description",
            priority=1,
            state="In Progress",
            branch_name="feature/task-7",
            url="https://example.com/7",
            labels=["bug", "urgent"],
            blocked_by=[blocker],
            created_at=now,
            updated_at=now,
            selected_agent="codex",
            knowledge_context="some context",
            graph_nodes=["node-a", "node-b"],
        )
        assert issue.description == "A description"
        assert issue.priority == 1
        assert issue.state == "In Progress"
        assert issue.branch_name == "feature/task-7"
        assert issue.url == "https://example.com/7"
        assert issue.labels == ["bug", "urgent"]
        assert len(issue.blocked_by) == 1
        assert issue.blocked_by[0].identifier == "TASK-99"
        assert issue.created_at == now
        assert issue.selected_agent == "codex"
        assert issue.knowledge_context == "some context"
        assert issue.graph_nodes == ["node-a", "node-b"]

    def test_orbit_extension_fields(self):
        """Orbit §5.1 extensions are present."""
        issue = Issue(id="1", identifier="ORB-1", title="Orbit task")
        # selected_agent, knowledge_context, graph_nodes are orbit extensions
        issue.selected_agent = "gemma"
        issue.knowledge_context = "ctx"
        issue.graph_nodes = ["n1"]
        assert issue.selected_agent == "gemma"
        assert issue.knowledge_context == "ctx"
        assert issue.graph_nodes == ["n1"]


# ---------------------------------------------------------------------------
# RunAttempt dataclass
# ---------------------------------------------------------------------------

class TestRunAttempt:
    def test_defaults(self):
        now = datetime.now()
        ra = RunAttempt(
            issue_id="1",
            issue_identifier="TASK-1",
            attempt=1,
            workspace_path="/tmp/ws",
            started_at=now,
        )
        assert ra.status == RunStatus.PREPARING_WORKSPACE
        assert ra.error is None
        assert ra.agent_name == ""
        assert ra.agent_mode == AgentMode.CLI
        assert ra.knowledge_injected is False
        assert ra.knowledge_ingested is False

    def test_attempt_can_be_none(self):
        ra = RunAttempt(
            issue_id="1",
            issue_identifier="TASK-1",
            attempt=None,
            workspace_path="/tmp/ws",
            started_at=datetime.now(),
        )
        assert ra.attempt is None

    def test_explicit_status(self):
        ra = RunAttempt(
            issue_id="1",
            issue_identifier="TASK-1",
            attempt=2,
            workspace_path="/tmp/ws",
            started_at=datetime.now(),
            status=RunStatus.SUCCEEDED,
        )
        assert ra.status == RunStatus.SUCCEEDED

    def test_orbit_extension_fields(self):
        """Orbit §5.2 extensions."""
        ra = RunAttempt(
            issue_id="1",
            issue_identifier="TASK-1",
            attempt=1,
            workspace_path="/tmp/ws",
            started_at=datetime.now(),
            agent_name="codex",
            agent_mode=AgentMode.APP_SERVER,
            knowledge_injected=True,
            knowledge_ingested=True,
        )
        assert ra.agent_name == "codex"
        assert ra.agent_mode == AgentMode.APP_SERVER
        assert ra.knowledge_injected is True
        assert ra.knowledge_ingested is True


# ---------------------------------------------------------------------------
# AgentTotals dataclass
# ---------------------------------------------------------------------------

class TestAgentTotals:
    def test_all_defaults_zero(self):
        totals = AgentTotals()
        assert totals.input_tokens == 0
        assert totals.output_tokens == 0
        assert totals.total_tokens == 0
        assert totals.seconds_running == 0.0

    def test_explicit_values(self):
        totals = AgentTotals(input_tokens=100, output_tokens=200, total_tokens=300, seconds_running=15.5)
        assert totals.input_tokens == 100
        assert totals.output_tokens == 200
        assert totals.total_tokens == 300
        assert totals.seconds_running == 15.5


# ---------------------------------------------------------------------------
# OrchestratorState dataclass
# ---------------------------------------------------------------------------

class TestOrchestratorState:
    def test_required_fields(self):
        state = OrchestratorState(poll_interval_ms=5000, max_concurrent_agents=3)
        assert state.poll_interval_ms == 5000
        assert state.max_concurrent_agents == 3

    def test_defaults(self):
        state = OrchestratorState(poll_interval_ms=1000, max_concurrent_agents=5)
        assert state.running == {}
        assert state.claimed == set()
        assert state.retry_attempts == {}
        assert state.completed == set()
        assert isinstance(state.agent_totals, AgentTotals)
        assert state.rate_limits is None

    def test_orbit_extensions(self):
        """Orbit §5.3 extensions."""
        state = OrchestratorState(poll_interval_ms=1000, max_concurrent_agents=5)
        assert state.agent_running_counts == {}
        assert state.knowledge_available is False

    def test_running_dict_is_independent(self):
        """Each OrchestratorState gets its own running dict."""
        a = OrchestratorState(poll_interval_ms=1000, max_concurrent_agents=1)
        b = OrchestratorState(poll_interval_ms=1000, max_concurrent_agents=1)
        a.running["x"] = None  # type: ignore[assignment]
        assert "x" not in b.running

    def test_claimed_is_set(self):
        state = OrchestratorState(poll_interval_ms=1000, max_concurrent_agents=1)
        state.claimed.add("TASK-1")
        assert "TASK-1" in state.claimed
