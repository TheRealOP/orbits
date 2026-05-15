"""Tests for the Linear GraphQL tracker adapter (orbit.tracker.linear)."""
from __future__ import annotations

import pytest
import respx
import httpx

from orbit.tracker.linear import LinearTracker
from orbit.config import TrackerConfig


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

ENDPOINT = "https://api.linear.app/graphql"


def make_cfg() -> TrackerConfig:
    cfg = TrackerConfig()
    cfg.kind = "linear"
    cfg.api_key = "fake-linear-key"
    cfg.project_slug = "my-project"
    cfg.endpoint = ENDPOINT
    cfg.active_states = ["Todo", "In Progress"]
    cfg.terminal_states = ["Done", "Cancelled"]
    return cfg


def gql_response(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def make_issue_node(
    id: str = "abc",
    identifier: str = "MT-1",
    title: str = "Fix bug",
    description: str = "desc",
    priority: int = 2,
    state_name: str = "Todo",
    labels: list[str] | None = None,
    relations: list[dict] | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    updated_at: str = "2026-01-02T00:00:00Z",
    branch_name: str = "fix/bug",
    url: str = "https://linear.app/team/issue/MT-1",
) -> dict:
    return {
        "id": id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "priority": priority,
        "url": url,
        "branchName": branch_name,
        "state": {"name": state_name},
        "labels": {"nodes": [{"name": lbl} for lbl in (labels or [])]},
        "relations": {"nodes": relations or []},
        "createdAt": created_at,
        "updatedAt": updated_at,
    }


def make_issues_page(
    nodes: list[dict],
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict:
    return {
        "issues": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
        }
    }


# ---------------------------------------------------------------------------
# fetch_candidate_issues — basic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_candidate_issues(tmp_path):
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(
            id="abc",
            identifier="MT-1",
            title="Fix bug",
            priority=2,
            labels=["Backend"],
        )
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].identifier == "MT-1"
    assert issues[0].priority == 2
    assert "backend" in issues[0].labels


@pytest.mark.asyncio
@respx.mock
async def test_fetch_candidate_issues_returns_empty_on_no_nodes():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_candidate_issues_state_parsed():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(id="x1", identifier="MT-2", state_name="In Progress"),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].state == "In Progress"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_pagination():
    """hasNextPage=True on first response triggers a second request."""
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = request.content.decode()
        if call_count == 1:
            return gql_response(make_issues_page(
                [make_issue_node(id="p1", identifier="MT-P1")],
                has_next_page=True,
                end_cursor="cursor-abc",
            ))
        else:
            return gql_response(make_issues_page(
                [make_issue_node(id="p2", identifier="MT-P2")],
                has_next_page=False,
            ))

    respx.post(ENDPOINT).mock(side_effect=side_effect)
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert call_count == 2
    identifiers = {i.identifier for i in issues}
    assert "MT-P1" in identifiers
    assert "MT-P2" in identifiers


@pytest.mark.asyncio
@respx.mock
async def test_pagination_missing_cursor_raises():
    """If hasNextPage is True but endCursor is None, should raise RuntimeError."""
    respx.post(ENDPOINT).mock(return_value=gql_response({
        "issues": {
            "nodes": [make_issue_node()],
            "pageInfo": {"hasNextPage": True, "endCursor": None},
        }
    }))
    tracker = LinearTracker(make_cfg())
    with pytest.raises(RuntimeError, match="linear_missing_end_cursor"):
        await tracker.fetch_candidate_issues()
    await tracker.aclose()


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_graphql_errors_raises():
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={
        "errors": [{"message": "Unauthorized"}]
    }))
    tracker = LinearTracker(make_cfg())
    with pytest.raises(RuntimeError, match="linear_graphql_errors"):
        await tracker.fetch_candidate_issues()
    await tracker.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_non_200_raises():
    respx.post(ENDPOINT).mock(return_value=httpx.Response(500, text="Internal Server Error"))
    tracker = LinearTracker(make_cfg())
    with pytest.raises(RuntimeError, match="linear_api_status"):
        await tracker.fetch_candidate_issues()
    await tracker.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_missing_data_field_raises():
    """A 200 response without a 'data' field should raise RuntimeError."""
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"something": "else"}))
    tracker = LinearTracker(make_cfg())
    with pytest.raises(RuntimeError, match="linear_unknown_payload"):
        await tracker.fetch_candidate_issues()
    await tracker.aclose()


# ---------------------------------------------------------------------------
# fetch_issues_by_states
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_issues_by_states():
    respx.post(ENDPOINT).mock(return_value=gql_response({
        "issues": {
            "nodes": [
                {"id": "d1", "identifier": "MT-D1", "state": {"name": "Done"}},
                {"id": "d2", "identifier": "MT-D2", "state": {"name": "Cancelled"}},
            ]
        }
    }))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_issues_by_states(["Done", "Cancelled"])
    await tracker.aclose()
    assert len(issues) == 2
    identifiers = {i.identifier for i in issues}
    assert "MT-D1" in identifiers
    assert "MT-D2" in identifiers


@pytest.mark.asyncio
async def test_empty_states_returns_empty():
    """fetch_issues_by_states([]) should not make an API call and return []."""
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_issues_by_states([])
    await tracker.aclose()
    assert issues == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_issues_by_states_single_state():
    respx.post(ENDPOINT).mock(return_value=gql_response({
        "issues": {
            "nodes": [
                {"id": "t1", "identifier": "MT-T1", "state": {"name": "Done"}},
            ]
        }
    }))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_issues_by_states(["Done"])
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].state == "Done"


# ---------------------------------------------------------------------------
# fetch_issue_states_by_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_issue_states_by_ids():
    respx.post(ENDPOINT).mock(return_value=gql_response({
        "nodes": [
            {"id": "id1", "identifier": "MT-1", "state": {"name": "Todo"}},
            {"id": "id2", "identifier": "MT-2", "state": {"name": "Done"}},
        ]
    }))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_issue_states_by_ids(["id1", "id2"])
    await tracker.aclose()
    assert len(issues) == 2
    states = {i.id: i.state for i in issues}
    assert states["id1"] == "Todo"
    assert states["id2"] == "Done"


@pytest.mark.asyncio
async def test_fetch_issue_states_by_ids_empty_returns_empty():
    """fetch_issue_states_by_ids([]) should not make an API call and return []."""
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_issue_states_by_ids([])
    await tracker.aclose()
    assert issues == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_issue_states_by_ids_skips_null_nodes():
    """Null nodes in the response (e.g. deleted issues) should be skipped."""
    respx.post(ENDPOINT).mock(return_value=gql_response({
        "nodes": [
            {"id": "id1", "identifier": "MT-1", "state": {"name": "Todo"}},
            None,
        ]
    }))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_issue_states_by_ids(["id1", "id-deleted"])
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].id == "id1"


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_labels_lowercased():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(labels=["Backend", "HIGH-PRIORITY", "Api"]),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert "backend" in issues[0].labels
    assert "high-priority" in issues[0].labels
    assert "api" in issues[0].labels


@pytest.mark.asyncio
@respx.mock
async def test_blockers_normalized():
    """Relations of type 'blocks' are parsed into blocked_by."""
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(relations=[
            {
                "relatedIssue": {
                    "id": "blocker-id",
                    "identifier": "MT-99",
                    "state": {"name": "In Progress"},
                }
            }
        ]),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues[0].blocked_by) == 1
    b = issues[0].blocked_by[0]
    assert b.id == "blocker-id"
    assert b.identifier == "MT-99"
    assert b.state == "In Progress"


@pytest.mark.asyncio
@respx.mock
async def test_blockers_empty_when_no_relations():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(relations=[]),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].blocked_by == []


@pytest.mark.asyncio
@respx.mock
async def test_multiple_blockers_parsed():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(relations=[
            {"relatedIssue": {"id": "b1", "identifier": "MT-10", "state": {"name": "Todo"}}},
            {"relatedIssue": {"id": "b2", "identifier": "MT-11", "state": {"name": "Done"}}},
        ]),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues[0].blocked_by) == 2


@pytest.mark.asyncio
@respx.mock
async def test_priority_zero_parsed():
    """Priority 0 is a valid value — should be stored as 0, not None."""
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(priority=0),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].priority == 0


@pytest.mark.asyncio
@respx.mock
async def test_priority_none_when_absent():
    node = make_issue_node()
    del node["priority"]
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([node])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].priority is None


@pytest.mark.asyncio
@respx.mock
async def test_branch_name_parsed():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(branch_name="feature/my-branch"),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].branch_name == "feature/my-branch"


@pytest.mark.asyncio
@respx.mock
async def test_url_parsed():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(url="https://linear.app/myteam/issue/MT-1"),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].url == "https://linear.app/myteam/issue/MT-1"


@pytest.mark.asyncio
@respx.mock
async def test_timestamps_parsed():
    respx.post(ENDPOINT).mock(return_value=gql_response(make_issues_page([
        make_issue_node(
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-06-15T12:30:00Z",
        ),
    ])))
    tracker = LinearTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].created_at is not None
    assert issues[0].updated_at is not None
    assert issues[0].created_at.year == 2026
    assert issues[0].updated_at.month == 6


# ---------------------------------------------------------------------------
# execute_graphql passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_execute_graphql_returns_data():
    respx.post(ENDPOINT).mock(return_value=gql_response({"custom": "value"}))
    tracker = LinearTracker(make_cfg())
    result = await tracker.execute_graphql("query { viewer { id } }")
    await tracker.aclose()
    assert result == {"custom": "value"}


@pytest.mark.asyncio
@respx.mock
async def test_execute_graphql_with_variables():
    respx.post(ENDPOINT).mock(return_value=gql_response({"result": True}))
    tracker = LinearTracker(make_cfg())
    result = await tracker.execute_graphql(
        "query Foo($id: ID!) { issue(id: $id) { title } }",
        variables={"id": "abc"},
    )
    await tracker.aclose()
    assert result == {"result": True}
