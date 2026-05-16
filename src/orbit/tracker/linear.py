"""Linear GraphQL tracker adapter — Symphony §11."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from ..config import TrackerConfig
from ..models import BlockerRef, Issue
from .base import TrackerAdapter

log = logging.getLogger(__name__)


_CANDIDATE_QUERY = """
query CandidateIssues($projectSlug: String!, $states: [String!]!, $after: String) {
  issues(
    filter: {
      project: { slugId: { eq: $projectSlug } }
      state: { name: { in: $states } }
    }
    first: 50
    after: $after
    orderBy: createdAt
  ) {
    nodes {
      id
      identifier
      title
      description
      priority
      url
      branchName
      state { name }
      labels { nodes { name } }
      inverseRelations(first: 50) {
        nodes {
          type
          issue {
            id
            identifier
            state { name }
          }
        }
      }
      createdAt
      updatedAt
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

_STATES_QUERY = """
query IssuesByState($projectSlug: String!, $states: [String!]!) {
  issues(
    filter: {
      project: { slugId: { eq: $projectSlug } }
      state: { name: { in: $states } }
    }
    first: 250
  ) {
    nodes {
      id
      identifier
      state { name }
    }
  }
}
"""

_BY_IDS_QUERY = """
query IssueStates($ids: [ID!]!, $first: Int!) {
  issues(filter: { id: { in: $ids } }, first: $first) {
    nodes {
      id
      identifier
      title
      description
      priority
      url
      branchName
      state { name }
      labels { nodes { name } }
      inverseRelations(first: 50) {
        nodes {
          type
          issue {
            id
            identifier
            state { name }
          }
        }
      }
      createdAt
      updatedAt
    }
  }
}
"""

_STATE_LOOKUP_QUERY = """
query ResolveStateId($issueId: String!, $stateName: String!) {
  issue(id: $issueId) {
    team {
      states(filter: { name: { eq: $stateName } }, first: 1) {
        nodes {
          id
        }
      }
    }
  }
}
"""

_UPDATE_STATE_MUTATION = """
mutation UpdateIssueState($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    success
  }
}
"""


def _parse_issue(node: dict) -> Issue:
    labels = [lbl["name"].lower() for lbl in node.get("labels", {}).get("nodes", [])]

    blockers: list[BlockerRef] = []
    relation_nodes = (
        node.get("inverseRelations", {}).get("nodes")
        or node.get("relations", {}).get("nodes", [])
    )
    for rel in relation_nodes:
        if rel.get("type") not in (None, "blocks"):
            continue
        ri = rel.get("issue") or rel.get("relatedIssue") or {}
        if ri:
            blockers.append(BlockerRef(
                id=ri.get("id"),
                identifier=ri.get("identifier"),
                state=(ri.get("state") or {}).get("name"),
            ))

    priority = node.get("priority")
    try:
        priority = int(priority) if priority is not None else None
    except (TypeError, ValueError):
        priority = None

    created_at = updated_at = None
    for field_name, attr in [("createdAt", "created_at"), ("updatedAt", "updated_at")]:
        raw = node.get(field_name)
        if raw:
            try:
                val = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if attr == "created_at":
                    created_at = val
                else:
                    updated_at = val
            except ValueError:
                pass

    return Issue(
        id=node["id"],
        identifier=node.get("identifier", node["id"]),
        title=node.get("title", ""),
        description=node.get("description"),
        priority=priority,
        state=(node.get("state") or {}).get("name", ""),
        branch_name=node.get("branchName"),
        url=node.get("url"),
        labels=labels,
        blocked_by=blockers,
        created_at=created_at,
        updated_at=updated_at,
    )


def _parse_minimal(node: dict) -> Issue | None:
    if not node or "__typename" not in node and "id" not in node:
        return None
    return Issue(
        id=node.get("id", ""),
        identifier=node.get("identifier", node.get("id", "")),
        title="",
        state=(node.get("state") or {}).get("name", ""),
    )


class LinearTracker(TrackerAdapter):
    """Linear GraphQL tracker adapter — Symphony §11.2."""

    def __init__(self, cfg: TrackerConfig):
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            headers={"Authorization": cfg.api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )
        self._endpoint = cfg.endpoint or "https://api.linear.app/graphql"

    async def _gql(self, query: str, variables: dict) -> dict[str, Any]:
        try:
            resp = await self._client.post(
                self._endpoint,
                json={"query": query, "variables": variables},
            )
        except httpx.RequestError as exc:
            raise RuntimeError(f"linear_api_request: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"linear_api_status: {resp.status_code} {resp.text[:200]}")

        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"linear_graphql_errors: {body['errors']}")

        data = body.get("data")
        if data is None:
            raise RuntimeError(f"linear_unknown_payload: no data field in response")
        return data

    async def fetch_candidate_issues(self) -> list[Issue]:
        issues: list[Issue] = []
        after: str | None = None
        while True:
            variables: dict[str, Any] = {
                "projectSlug": self._cfg.project_slug,
                "states": self._cfg.active_states,
                "after": after,
            }
            data = await self._gql(_CANDIDATE_QUERY, variables)
            page = data.get("issues", {})
            nodes = page.get("nodes", [])
            issues.extend(_parse_issue(n) for n in nodes)
            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                raise RuntimeError("linear_missing_end_cursor")
            after = cursor
        return issues

    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        if not state_names:
            return []
        data = await self._gql(
            _STATES_QUERY,
            {"projectSlug": self._cfg.project_slug, "states": state_names},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        return [_parse_minimal(n) for n in nodes if n]  # type: ignore[misc]

    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        if not issue_ids:
            return []
        requested_ids = list(dict.fromkeys(issue_ids))
        result: list[Issue] = []
        for start in range(0, len(requested_ids), 50):
            batch = requested_ids[start:start + 50]
            data = await self._gql(
                _BY_IDS_QUERY,
                {"ids": batch, "first": len(batch)},
            )
            nodes = data.get("issues", {}).get("nodes", [])
            result.extend(_parse_issue(n) for n in nodes if n)

        order = {issue_id: index for index, issue_id in enumerate(requested_ids)}
        return sorted(result, key=lambda issue: order.get(issue.id, len(order)))

    async def execute_graphql(self, query: str, variables: dict | None = None) -> dict:
        """linear_graphql client-side tool — Symphony §10.5."""
        return await self._gql(query, variables or {})

    async def transition_issue_state(self, issue_id: str, state_name: str) -> bool:
        lookup = await self._gql(
            _STATE_LOOKUP_QUERY,
            {"issueId": issue_id, "stateName": state_name},
        )
        state_id = (
            lookup.get("issue", {})
            .get("team", {})
            .get("states", {})
            .get("nodes", [{}])[0]
            .get("id")
        )
        if not state_id:
            raise RuntimeError(f"linear_state_not_found: {state_name}")

        result = await self._gql(
            _UPDATE_STATE_MUTATION,
            {"issueId": issue_id, "stateId": state_id},
        )
        return result.get("issueUpdate", {}).get("success") is True

    async def aclose(self) -> None:
        await self._client.aclose()
