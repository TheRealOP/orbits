import json
from pathlib import Path
import tempfile
import unittest

from orbits.agents.interface import router


class _FakeBus:
    def __init__(self):
        self.sent = []

    async def send(self, from_agent, to_agent, msg_type, payload, priority=5):
        self.sent.append(
            {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "msg_type": msg_type,
                "payload": payload,
                "priority": priority,
            }
        )
        return 1


class TestInterfaceRouter(unittest.IsolatedAsyncioTestCase):
    def _config(self, base: Path) -> dict:
        return {
            "daemon": {
                "status_file": str(base / "state" / "model_status.json"),
                "events_log": str(base / "state" / "events.jsonl"),
                "state_dir": str(base / "state"),
                "claude_log_dir": str(base / "logs"),
                "poll_interval_seconds": 1,
            },
            "models": {},
        }

    def _write_status(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    async def test_routes_dual_when_both_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            self._write_status(base / "state" / "model_status.json", {"claude_sonnet": "active", "gpt_5_4": "active"})
            bus = _FakeBus()
            result = await router.route_task("test", config=config, bus=bus)
        self.assertEqual(result["mode"], "dual")
        self.assertEqual(bus.sent[0]["payload"]["mode"], "dual")
        self.assertIn("task_id", bus.sent[0]["payload"])
        self.assertIn("both available", result["message"])

    async def test_routes_gpt_only_when_claude_rate_limited(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            self._write_status(base / "state" / "model_status.json", {"claude_sonnet": "rate_limited", "gpt_5_4": "active"})
            bus = _FakeBus()
            result = await router.route_task("test", config=config, bus=bus)
        self.assertEqual(result["mode"], "gpt_only")
        self.assertIn("GPT fallback path", result["message"])

    async def test_routes_claude_only_when_gpt_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            self._write_status(base / "state" / "model_status.json", {"claude_sonnet": "active", "gpt_5_4": "error"})
            bus = _FakeBus()
            result = await router.route_task("test", config=config, bus=bus)
        self.assertEqual(result["mode"], "claude_only")
        self.assertIn("Claude-only path", result["message"])

    async def test_queues_when_both_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            self._write_status(base / "state" / "model_status.json", {"claude_sonnet": "rate_limited", "gpt_5_4": "error"})
            result = await router.route_task("test", config=config)
            queue_path = base / "state" / "task_queue.jsonl"
            self.assertTrue(queue_path.exists())
        self.assertEqual(result["mode"], "queued")
        self.assertIn("task_id", result)
        self.assertIn("queued the task", result["message"])

    async def test_queues_when_status_file_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            result = await router.route_task("test", config=config)
        self.assertEqual(result["mode"], "queued")

    async def test_queues_when_status_file_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            status_path = base / "state" / "model_status.json"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text("not-json", encoding="utf-8")
            result = await router.route_task("test", config=config)
        self.assertEqual(result["mode"], "queued")

    async def test_queue_append_behavior(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config = self._config(base)
            await router.route_task("task one", config=config)
            await router.route_task("task two", config=config)
            queue_path = base / "state" / "task_queue.jsonl"
            lines = queue_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
