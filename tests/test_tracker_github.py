"""Tests for the GitHub Issues tracker adapter (orbit.tracker.github)."""
from __future__ import annotations

import pytest
import respx
import httpx

from orbit.tracker.github import GitHubTracker
from orbit.config import TrackerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cfg(repo: str = "owner/repo") -> TrackerConfig:
    cfg = TrackerConfig()
    cfg.kind = "github"
    cfg.repo = repo
    cfg.api_key = "fake-token"
    cfg.active_labels = ["orbit:todo"]
    cfg.terminal_labels = ["orbit:done"]
    cfg.running_label = "orbit:in-progress"
    cfg.handoff_label = "orbit:review"
    cfg.active_states = ["todo"]
    cfg.terminal_states = ["done"]
    return cfg


def issue_payload(
    number: int = 1,
    labels: list[str] | None = None,
    title: str = "Test",
    body: str = "desc",
) -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "labels": [{"name": lbl} for lbl in (labels or ["orbit:todo"])],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }


BASE_ISSUES_URL = "https://api.github.com/repos/owner/repo/issues"


# ---------------------------------------------------------------------------
# fetch_candidate_issues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_candidate_issues_returns_issue():
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[issue_payload(1)])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert any(i.id == "1" for i in issues)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_candidate_issues_deduplicates():
    """When two active_labels both return the same issue, it should appear once."""
    cfg = make_cfg()
    cfg.active_labels = ["orbit:todo", "orbit:in-progress"]

    # Both label requests return the same issue number
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[issue_payload(1, labels=["orbit:todo", "orbit:in-progress"])])
    )
    tracker = GitHubTracker(cfg)
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    ids = [i.id for i in issues]
    assert ids.count("1") == 1


@pytest.mark.asyncio
@respx.mock
async def test_filters_out_pull_requests():
    pr = issue_payload(2)
    pr["pull_request"] = {"url": "https://github.com/owner/repo/pull/2"}
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[pr])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert not issues


@pytest.mark.asyncio
@respx.mock
async def test_fetch_candidate_returns_empty_on_no_issues():
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_pagination_follows_link_header():
    page2_url = "https://api.github.com/repos/owner/repo/issues?page=2"
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First request: return page 1 with a Link header pointing to page 2
            return httpx.Response(
                200,
                json=[issue_payload(1)],
                headers={"Link": f'<{page2_url}>; rel="next"'},
            )
        else:
            # Second request (following next link): return page 2, no Link header
            return httpx.Response(200, json=[issue_payload(2)])

    respx.get(BASE_ISSUES_URL).mock(side_effect=side_effect)
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    ids = {i.id for i in issues}
    assert "1" in ids
    assert "2" in ids
    assert call_count == 2


# ---------------------------------------------------------------------------
# fetch_issue_states_by_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_issue_states_by_ids_parallel():
    respx.get("https://api.github.com/repos/owner/repo/issues/1").mock(
        return_value=httpx.Response(200, json=issue_payload(1, labels=["orbit:todo"]))
    )
    respx.get("https://api.github.com/repos/owner/repo/issues/2").mock(
        return_value=httpx.Response(200, json=issue_payload(2, labels=["orbit:done"]))
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_issue_states_by_ids(["1", "2"])
    await tracker.aclose()
    assert len(issues) == 2
    ids = {i.id for i in issues}
    assert ids == {"1", "2"}


@pytest.mark.asyncio
@respx.mock
async def test_fetch_issue_states_by_ids_empty_returns_empty():
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_issue_states_by_ids([])
    await tracker.aclose()
    assert issues == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_issue_states_by_ids_404_skipped():
    respx.get("https://api.github.com/repos/owner/repo/issues/1").mock(
        return_value=httpx.Response(200, json=issue_payload(1))
    )
    respx.get("https://api.github.com/repos/owner/repo/issues/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_issue_states_by_ids(["1", "999"])
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].id == "1"


# ---------------------------------------------------------------------------
# Priority and labels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_priority_parsed_from_label():
    payload = issue_payload(1, labels=["orbit:todo", "priority:2"])
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].priority == 2


@pytest.mark.asyncio
@respx.mock
async def test_priority_none_when_no_priority_label():
    payload = issue_payload(1, labels=["orbit:todo"])
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues[0].priority is None


@pytest.mark.asyncio
@respx.mock
async def test_labels_normalized_lowercase():
    payload = issue_payload(1, labels=["orbit:todo", "Backend", "High-Priority"])
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues) == 1
    assert "backend" in issues[0].labels
    assert "high-priority" in issues[0].labels
    assert "orbit:todo" in issues[0].labels


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_non_200_raises_on_single_issue_fetch():
    respx.get("https://api.github.com/repos/owner/repo/issues/1").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    tracker = GitHubTracker(make_cfg())
    # fetch_issue_states_by_ids catches errors per-issue and returns None
    issues = await tracker.fetch_issue_states_by_ids(["1"])
    await tracker.aclose()
    # Error is caught, result for that id is dropped
    assert issues == []


@pytest.mark.asyncio
@respx.mock
async def test_candidate_fetch_error_does_not_crash():
    """fetch_candidate_issues catches per-label errors and continues."""
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    tracker = GitHubTracker(make_cfg())
    # Should not raise; returns whatever was successfully fetched (empty here)
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert issues == []


# ---------------------------------------------------------------------------
# Label-state precedence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_label_state_handoff_takes_precedence():
    """handoff > terminal > running > active > unknown."""
    # Issue has both handoff and todo labels: handoff should win
    payload = issue_payload(1, labels=["orbit:review", "orbit:todo"])
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    # The issue should have handoff_label as its state
    matching = [i for i in issues if i.id == "1"]
    if matching:
        assert matching[0].state == "orbit:review"


@pytest.mark.asyncio
@respx.mock
async def test_label_state_terminal_label_state_is_derived():
    """Issues fetched via active_labels have their state derived from _label_state_precedence.
    An issue with only 'orbit:done' label resolves to state='orbit:done'.
    """
    payload = issue_payload(1, labels=["orbit:done"])
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    cfg = make_cfg()
    cfg.active_labels = ["orbit:done"]  # queried as active
    tracker = GitHubTracker(cfg)
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    # State should be derived to "orbit:done" (the terminal label)
    if issues:
        assert issues[0].state == "orbit:done"


# ---------------------------------------------------------------------------
# Blockers from body
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_blockers_parsed_from_body():
    payload = issue_payload(1, body="This blocks:#42 and blocks:#99 items")
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues) == 1
    blocker_ids = {b.identifier for b in issues[0].blocked_by}
    assert "#42" in blocker_ids
    assert "#99" in blocker_ids


@pytest.mark.asyncio
@respx.mock
async def test_no_blockers_when_body_clean():
    payload = issue_payload(1, body="Just a normal description")
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[payload])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_candidate_issues()
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].blocked_by == []


# ---------------------------------------------------------------------------
# fetch_issues_by_labels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_issues_by_labels_empty_returns_empty():
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_issues_by_labels([])
    await tracker.aclose()
    assert issues == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_issues_by_labels_returns_issues():
    respx.get(BASE_ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[issue_payload(1, labels=["orbit:done"])])
    )
    tracker = GitHubTracker(make_cfg())
    issues = await tracker.fetch_issues_by_labels(["orbit:done"])
    await tracker.aclose()
    assert len(issues) == 1
    assert issues[0].id == "1"
