"""Agent selection and routing engine — Orbit §3.2–§3.3."""
from __future__ import annotations

import asyncio
import logging

from .config import AgentsConfig, RoutingConfig
from .models import Issue

log = logging.getLogger(__name__)


def select_agent(
    issue: Issue,
    agents_cfg: AgentsConfig,
    routing_cfg: RoutingConfig,
) -> str:
    """Native agent selection (§3.2 priority 1 + 3 + 4, no external router)."""
    # Priority 1: explicit label override agent:<name>
    for label in issue.labels:
        if label.startswith("agent:"):
            name = label[len("agent:"):]
            if name in agents_cfg.registry or name == agents_cfg.default:
                log.info(
                    "agent_selected_by_label issue=%s agent=%s",
                    issue.identifier, name,
                )
                return name

    # Priority 3: capability matching
    registry = agents_cfg.registry
    if registry:
        best_name: str | None = None
        best_weight = -1
        best_matches = 0
        for agent_name, entry in registry.items():
            matches = sum(
                1 for cap in entry.capabilities
                if cap in issue.labels or any(cap in lbl for lbl in issue.labels)
            )
            if matches > best_matches or (matches == best_matches and entry.weight > best_weight):
                best_matches = matches
                best_name = agent_name
                best_weight = entry.weight
        if best_name and best_matches > 0:
            log.info(
                "agent_selected_by_capability issue=%s agent=%s matches=%d",
                issue.identifier, best_name, best_matches,
            )
            return best_name

    # Priority 4: default fallback
    log.info("agent_selected_default issue=%s agent=%s", issue.identifier, agents_cfg.default)
    return agents_cfg.default


async def select_agent_with_router(
    issue: Issue,
    agents_cfg: AgentsConfig,
    routing_cfg: RoutingConfig,
) -> str:
    """Full agent selection including external router (§3.2 full priority order)."""
    # Priority 1: explicit label override
    for label in issue.labels:
        if label.startswith("agent:"):
            name = label[len("agent:"):]
            if name in agents_cfg.registry or name == agents_cfg.default:
                return name

    # Priority 2: external router command
    if routing_cfg.command:
        agent = await _run_external_router(issue, routing_cfg)
        if agent and (agent in agents_cfg.registry or agent == agents_cfg.default):
            log.info("agent_selected_by_router issue=%s agent=%s", issue.identifier, agent)
            return agent

    # Priority 3 + 4: native selection
    return select_agent(issue, agents_cfg, routing_cfg)


async def _run_external_router(issue: Issue, routing_cfg: RoutingConfig) -> str | None:
    """Run the external routing command with issue info on stdin."""
    stdin_text = f"{issue.title}: {issue.description or ''}".encode()
    timeout_s = routing_cfg.timeout_ms / 1000.0

    try:
        proc = await asyncio.create_subprocess_shell(
            routing_cfg.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(stdin_text), timeout=timeout_s
        )
        result = stdout.decode().strip().lower()
        return result if result else None
    except asyncio.TimeoutError:
        log.warning("router_timeout command=%s fallback=%s", routing_cfg.command, routing_cfg.fallback)
        return routing_cfg.fallback
    except Exception as exc:
        log.warning("router_error command=%s error=%s fallback=%s", routing_cfg.command, exc, routing_cfg.fallback)
        return routing_cfg.fallback
