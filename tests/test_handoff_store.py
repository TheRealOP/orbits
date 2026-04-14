import json
from pathlib import Path
import tempfile
import unittest

from orbits.handoff import store


class TestHandoffStore(unittest.TestCase):
    def _config(self, base: Path) -> dict:
        return {
            "daemon": {
                "state_dir": str(base / "state"),
                "status_file": str(base / "state" / "model_status.json"),
                "events_log": str(base / "state" / "events.jsonl"),
                "claude_log_dir": str(base / "logs"),
                "poll_interval_seconds": 1,
            },
            "models": {},
        }

    def test_write_and_read_plan_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir))
            store.write_task_record("task-1", "plan", {"step": 1}, config)
            record = store.read_task_record("task-1", "plan", config)
        self.assertEqual(record["payload"]["step"], 1)

    def test_write_and_read_handoff_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir))
            store.write_task_record("task-1", "handoff", {"next": "resume"}, config)
            record = store.read_task_record("task-1", "handoff", config)
        self.assertEqual(record["payload"]["next"], "resume")

    def test_write_and_read_decisions_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir))
            store.write_task_record("task-1", "decisions", {"choice": "A"}, config)
            record = store.read_task_record("task-1", "decisions", config)
        self.assertEqual(record["payload"]["choice"], "A")

    def test_write_and_read_session_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir))
            store.write_session_owner("claude", "task-1", config)
            record = store.read_session_owner(config)
        self.assertEqual(record["owner"], "claude")

    def test_set_pending_handoff_true(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir))
            path = Path(config["daemon"]["status_file"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"claude_sonnet": "active"}), encoding="utf-8")
            store.set_pending_handoff(True, config)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(data["pending_handoff"])
        self.assertEqual(data["claude_sonnet"], "active")

    def test_set_pending_handoff_false(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir))
            path = Path(config["daemon"]["status_file"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"pending_handoff": True, "gpt_5_4": "active"}), encoding="utf-8")
            store.set_pending_handoff(False, config)
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(data["pending_handoff"])
        self.assertEqual(data["gpt_5_4"], "active")


if __name__ == "__main__":
    unittest.main()
