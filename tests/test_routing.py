"""Tests for orbit.routing — select_agent and select_agent_with_router."""
import pytest
from orbit.routing import select_agent, select_agent_with_router
from orbit.config import AgentsConfig, AgentEntry, RoutingConfig
from orbit.models import Issue


def make_agents_cfg(default="codex", registry=None) -> AgentsConfig:
    cfg = AgentsConfig()
    cfg.default = default
    cfg.registry = registry or {}
    return cfg


def make_routing_cfg(command=None, fallback="codex") -> RoutingConfig:
    cfg = RoutingConfig()
    cfg.command = command
    cfg.timeout_ms = 5000
    cfg.fallback = fallback
    return cfg


def make_registry(**agents) -> dict:
    result = {}
    for name, (caps, weight) in agents.items():
        e = AgentEntry()
        e.capabilities = caps
        e.weight = weight
        result[name] = e
    return result


def test_explicit_label_override_wins():
    registry = make_registry(claude=(["reasoning"], 8), codex=(["code"], 10))
    cfg = make_agents_cfg("codex", registry)
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["agent:claude"])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "claude"


def test_capability_matching_picks_best_weight():
    registry = make_registry(
        claude=(["reasoning", "planning"], 8),
        codex=(["code", "testing"], 10),
    )
    cfg = make_agents_cfg("codex", registry)
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["code"])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "codex"


def test_default_fallback_when_no_match():
    cfg = make_agents_cfg("gemini", {})
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=[])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "gemini"


def test_label_override_unknown_agent_falls_through():
    # agent:unknown → no registry match → falls through to capability/default
    cfg = make_agents_cfg("codex", {})
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["agent:unknown"])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "codex"  # falls to default


@pytest.mark.asyncio
async def test_external_router_used_when_configured():
    registry = make_registry(claude=(["reasoning"], 8))
    cfg = make_agents_cfg("codex", registry)
    routing = make_routing_cfg(command="echo claude", fallback="codex")
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=[])
    result = await select_agent_with_router(issue, cfg, routing)
    assert result == "claude"


@pytest.mark.asyncio
async def test_external_router_fallback_on_timeout():
    # Use a command that sleeps longer than timeout.
    # The router times out → returns routing_cfg.fallback ("gemini").
    # select_agent_with_router accepts the router result only when the agent
    # is in the registry or equals the default.  Set default="gemini" so it
    # passes the membership check and is actually returned.
    routing = make_routing_cfg(command="sleep 60", fallback="gemini")
    routing.timeout_ms = 100
    cfg = make_agents_cfg("gemini", {})  # default matches the fallback
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=[])
    result = await select_agent_with_router(issue, cfg, routing)
    assert result == "gemini"


@pytest.mark.asyncio
async def test_external_router_unknown_output_falls_through():
    routing = make_routing_cfg(command="echo unknown_agent_xyz", fallback="codex")
    registry = make_registry(codex=(["code"], 10))
    cfg = make_agents_cfg("codex", registry)
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=[])
    result = await select_agent_with_router(issue, cfg, routing)
    # unknown output → native selection → default
    assert result == "codex"


def test_no_registry_returns_default():
    cfg = make_agents_cfg("default_agent", {})
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=[])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "default_agent"


def test_label_override_for_default_agent_wins():
    # agent:<default> is in registry or equals default → should still win
    cfg = make_agents_cfg("codex", {})
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["agent:codex"])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "codex"


def test_higher_weight_wins_when_equal_capability_matches():
    registry = make_registry(
        light=(["code"], 3),
        heavy=(["code"], 9),
    )
    cfg = make_agents_cfg("light", registry)
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["code"])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "heavy"


def test_multiple_capability_matches_win_over_fewer():
    registry = make_registry(
        specialist=(["code", "testing"], 5),
        generalist=(["code"], 10),
    )
    cfg = make_agents_cfg("generalist", registry)
    # Both labels present → specialist has 2 matches vs generalist's 1
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["code", "testing"])
    result = select_agent(issue, cfg, make_routing_cfg())
    assert result == "specialist"


@pytest.mark.asyncio
async def test_no_router_command_skips_external_call():
    # routing.command is None → should skip external router, fall through to native
    registry = make_registry(codex=(["code"], 10))
    cfg = make_agents_cfg("codex", registry)
    routing = make_routing_cfg(command=None, fallback="codex")
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=[])
    result = await select_agent_with_router(issue, cfg, routing)
    assert result == "codex"


@pytest.mark.asyncio
async def test_label_override_takes_priority_over_external_router():
    # Even with router configured, explicit agent: label should win
    registry = make_registry(claude=(["reasoning"], 8), codex=(["code"], 10))
    cfg = make_agents_cfg("codex", registry)
    # router would return "codex" but label says "claude"
    routing = make_routing_cfg(command="echo codex", fallback="codex")
    issue = Issue(id="1", identifier="T-1", title="x", state="todo", labels=["agent:claude"])
    result = await select_agent_with_router(issue, cfg, routing)
    assert result == "claude"
