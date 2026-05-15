"""Runner event types — Orbit §3.4."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunnerEventType(str, Enum):
    SESSION_STARTED = "session_started"
    STREAMING = "streaming"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_TIMEOUT = "turn_timeout"
    STALLED = "stalled"
    STARTUP_FAILED = "startup_failed"


@dataclass
class RunnerEvent:
    event: RunnerEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pid: int | None = None
    message: str = ""
    exit_code: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
