"""GitHub Issues tracker adapter — Orbit §2."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from ..config import TrackerConfig
from ..models import BlockerRef, Issue
from .base import TrackerAdapter

log = logging.getLogger(__name__)

_PRIORITY_RE = re.compile(r"\bpriority:(\d+)\b", re.IGNORECASE)
_BLOCKS_RE = re.compile(r"\bblocks:#(\d+)\b", re.IGNORECASE)


def _label_state_precedence(
    labels: list[str],
    handoff_label: str,
    terminal_labels: list[str],
    running_label: str,
    active_labels: list[str],
) -> str:
    label_set = set(labels)
    if handoff_label in label_set:
        return handoff_label
    for tl in terminal_labels:
        if tl in label_set:
            return tl
    if running_label in label_set:
        return running_label
    for al in active_labels:
        if al in label_set:
            return al
    return "unknown"


def _parse_issue(raw: dict, cfg: TrackerConfig) -> Issue:
    # Skip PRs
    if "pull_request" in raw:
        return None  # type: ignore[return-value]

    number = str(raw["number"])
    labels = [lbl["name"].lower() for lbl in raw.get("labels", [])]

    priority: int | None = None
    for lbl in labels:
        m = _PRIORITY_RE.search(lbl)
        if m:
            priority = int(m.group(1))
            break

    state = _label_state_precedence(
        labels,
        cfg.handoff_label,
        cfg.terminal_labels,
        cfg.running_label,
        cfg.active_labels,
    )

    # Parse blockers from body convention: "blocks:#123"
    body = raw.get("body") or ""
    blockers: list[BlockerRef] = []
    for m in _BLOCKS_RE.finditer(body):
        blockers.append(BlockerRef(identifier=f"#{m.group(1)}"))

    created_at: datetime | None = None
    updated_at: datetime | None = None
    try:
        created_at = datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00"))
        updated_at = datetime.fromisoformat(raw["updated_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        pass

    return Issue(
        id=number,
        identifier=number,
        title=raw.get("title", ""),
        description=body or None,
        priority=priority,
        state=state,
        url=raw.get("html_url"),
        labels=labels,
        blocked_by=blockers,
        created_at=created_at,
        updated_at=updated_at,
    )


class GitHubTracker(TrackerAdapter):
    """Queries GitHub Issues API — Orbit §2.1–§2.4."""

    BASE_URL = "https://api.github.com"

    def __init__(self, cfg: TrackerConfig):
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        self._active_labels = list(cfg.active_labels)
        self._terminal_labels = list(cfg.terminal_labels)
        self._active_states_lower = [s.lower() for s in cfg.active_states]
        self._terminal_states_lower = [s.lower() for s in cfg.terminal_states]

    async def _get_page(self, url: str, params: dict | None = None) -> tuple[list[dict], str | None]:
        resp = await self._client.get(url, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"github_api_status: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        next_url: str | None = None
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    next_url = m.group(1)
        return data, next_url

    async def _fetch_all_by_label(self, label: str) -> list[dict]:
        url = f"{self.BASE_URL}/repos/{self._cfg.repo}/issues"
        params: dict[str, Any] = {
            "state": "open",
            "labels": label,
            "per_page": 50,
        }
        items: list[dict] = []
        while url:
            page, next_url = await self._get_page(url, params if params else None)
            items.extend(page)
            url = next_url  # type: ignore[assignment]
            params = None  # params only on first request
        return items

    async def fetch_candidate_issues(self) -> list[Issue]:
        """Orbit §2.2: OR semantics across active_labels with deduplication."""
        seen: dict[int, dict] = {}
        for label in self._active_labels:
            try:
                items = await self._fetch_all_by_label(label)
            except Exception as exc:
                log.error("github_fetch_failed label=%s error=%s", label, exc)
                continue
            for item in items:
                if "pull_request" not in item:
                    seen[item["number"]] = item

        issues: list[Issue] = []
        for raw in seen.values():
            issue = _parse_issue(raw, self._cfg)
            if issue:
                # Include only issues in active states (not terminal)
                state_lower = issue.state.lower()
                if (
                    state_lower not in self._terminal_states_lower
                    and state_lower in [lbl.lower() for lbl in self._active_labels]
                ):
                    issues.append(issue)
                elif issue.state in self._cfg.active_labels:
                    issues.append(issue)
                else:
                    # State derived from labels — include if handoff/running/active label
                    if issue.state not in self._cfg.terminal_labels:
                        issues.append(issue)
        return issues

    async def fetch_issues_by_labels(self, label_names: list[str]) -> list[Issue]:
        if not label_names:
            return []
        seen: dict[int, dict] = {}
        for label in label_names:
            try:
                items = await self._fetch_all_by_label(label)
            except Exception as exc:
                log.error("github_fetch_by_label_failed label=%s error=%s", label, exc)
                continue
            for item in items:
                if "pull_request" not in item:
                    seen[item["number"]] = item
        return [_parse_issue(r, self._cfg) for r in seen.values() if _parse_issue(r, self._cfg)]

    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        # For GitHub, states map to terminal labels
        return await self.fetch_issues_by_labels(self._cfg.terminal_labels)

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        """Orbit §2.2: parallel fetch per issue, max 10 concurrent."""
        sem = asyncio.Semaphore(10)

        async def fetch_one(issue_id: str) -> Issue | None:
            async with sem:
                url = f"{self.BASE_URL}/repos/{self._cfg.repo}/issues/{issue_id}"
                try:
                    resp = await self._client.get(url)
                    if resp.status_code == 404:
                        return None
                    if resp.status_code != 200:
                        raise RuntimeError(f"github_api_status: {resp.status_code}")
                    return _parse_issue(resp.json(), self._cfg)
                except Exception as exc:
                    log.error("github_state_fetch_failed id=%s error=%s", issue_id, exc)
                    return None

        results = await asyncio.gather(*[fetch_one(iid) for iid in issue_ids])
        return [r for r in results if r is not None]

    async def aclose(self) -> None:
        await self._client.aclose()
