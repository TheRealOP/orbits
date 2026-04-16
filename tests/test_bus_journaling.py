import json
import tempfile
from pathlib import Path

import pytest

from orchestrator.core.bus import MessageBus, MsgType


@pytest.mark.anyio
async def test_bus_journals_agent_and_misc_messages():
    with tempfile.TemporaryDirectory() as temp_dir:
        bus = MessageBus(Path(temp_dir) / "bus.db")
        await bus.init()

        await bus.send("agent1", "agent2", MsgType.CONTEXT_REQUEST, {"topic": "x"})
        await bus.send("worker_test", "monitor", MsgType.HEARTBEAT, {"ok": True})
        await bus.receive("agent2", msg_types=[MsgType.CONTEXT_REQUEST], limit=1)
        await bus.close()

    repo_root = Path(__file__).resolve().parents[1]
    agent1_log = repo_root / "Knowledge" / "logs" / "agent1" / f"{__import__('datetime').datetime.now(__import__('datetime').UTC).date().isoformat()}.jsonl"
    misc_log = repo_root / "Knowledge" / "logs" / "misc_agent" / f"{__import__('datetime').datetime.now(__import__('datetime').UTC).date().isoformat()}.jsonl"

    assert agent1_log.exists()
    assert misc_log.exists()

    agent_entries = [json.loads(line) for line in agent1_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    misc_entries = [json.loads(line) for line in misc_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert any(entry["event"] == "send" and entry["from_agent"] == "agent1" for entry in agent_entries)
    assert any(entry["event"] == "send" and entry["from_agent"] == "worker_test" for entry in misc_entries)
