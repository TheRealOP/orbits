"""Abstract tracker adapter — Symphony §11.1."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Issue


class TrackerAdapter(ABC):
    @abstractmethod
    async def fetch_candidate_issues(self) -> list[Issue]:
        """Fetch issues in active states for dispatch."""

    @abstractmethod
    async def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        """Fetch issues in given states (used for startup terminal cleanup)."""

    @abstractmethod
    async def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        """Fetch current state for a list of issue IDs (reconciliation)."""

    # Labels variant for GitHub — no-op by default for other trackers
    async def fetch_issues_by_labels(self, label_names: list[str]) -> list[Issue]:
        return []

    async def transition_issue_state(self, issue_id: str, state_name: str) -> bool:
        """Move an issue to a tracker state when supported."""
        return False
