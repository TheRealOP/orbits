"""Tracker adapter package."""
from .base import TrackerAdapter
from .file import FileTracker
from .github import GitHubTracker
from .linear import LinearTracker

__all__ = ["TrackerAdapter", "FileTracker", "GitHubTracker", "LinearTracker", "make_tracker"]


def make_tracker(cfg: "ServiceConfig") -> TrackerAdapter:  # noqa: F821
    from ..config import ServiceConfig  # local import to avoid cycles
    assert isinstance(cfg, ServiceConfig)
    kind = cfg.tracker.kind
    if kind == "linear":
        return LinearTracker(cfg.tracker)
    elif kind == "github":
        return GitHubTracker(cfg.tracker)
    elif kind == "file":
        return FileTracker(cfg.tracker)
    else:
        raise ValueError(f"unsupported_tracker_kind: {kind!r}")
