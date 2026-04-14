import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orbits.daemon import monitor


class TestMonitorDaemon(unittest.TestCase):
    def test_load_config_uses_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps({"daemon": {"poll_interval_seconds": 5}}), encoding="utf-8")
            config = monitor.load_config(config_path)
        self.assertEqual(config["daemon"]["poll_interval_seconds"], 5)
        self.assertIn("status_file", config["daemon"])

    def test_write_status_writes_status_and_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "daemon": {
                    "status_file": str(Path(temp_dir) / "state" / "model_status.json"),
                    "events_log": str(Path(temp_dir) / "state" / "model_status_events.jsonl"),
                    "state_dir": str(Path(temp_dir) / "state"),
                    "claude_log_dir": str(Path(temp_dir) / "logs"),
                    "opencode_event_dir": str(Path(temp_dir) / "opencode"),
                    "poll_interval_seconds": 1,
                },
                "models": monitor.DEFAULT_CONFIG["models"],
            }
            status = monitor.ModelStatuses("active", "active", "active", "active", "now")
            monitor.write_status(status, config)
            self.assertTrue(Path(config["daemon"]["status_file"]).exists())
            self.assertTrue(Path(config["daemon"]["events_log"]).exists())

    def test_write_status_does_not_append_duplicate_event_for_timestamp_only_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "daemon": {
                    "status_file": str(Path(temp_dir) / "state" / "model_status.json"),
                    "events_log": str(Path(temp_dir) / "state" / "model_status_events.jsonl"),
                    "state_dir": str(Path(temp_dir) / "state"),
                    "claude_log_dir": str(Path(temp_dir) / "logs"),
                    "opencode_event_dir": str(Path(temp_dir) / "opencode"),
                    "poll_interval_seconds": 1,
                },
                "models": monitor.DEFAULT_CONFIG["models"],
            }
            first = monitor.ModelStatuses("active", "active", "active", "active", "t1")
            second = monitor.ModelStatuses("active", "active", "active", "active", "t2")
            monitor.write_status(first, config)
            monitor.write_status(second, config)
            lines = Path(config["daemon"]["events_log"]).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)

    def test_detect_claude_rate_limit_signal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "recent.log"
            log_path.write_text("HTTP 429 rate_limit reached", encoding="utf-8")
            state = monitor.detect_claude(Path(temp_dir), lookback_minutes=10)
        self.assertEqual(state, "rate_limited")

    def test_detect_claude_active_without_rate_limit_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "recent.log"
            log_path.write_text("normal output", encoding="utf-8")
            state = monitor.detect_claude(Path(temp_dir), lookback_minutes=10)
        self.assertEqual(state, "active")

    def test_run_once_exits_cleanly_when_ram_is_critical(self):
        config = monitor.DEFAULT_CONFIG
        daemon = monitor.MonitorDaemon(config)
        with patch("orbits.daemon.monitor.gate_launch") as gate_launch_mock, patch(
            "orbits.daemon.monitor.write_status"
        ) as write_status_mock:
            gate_launch_mock.return_value = type("Decision", (), {"state": "critical", "total_used_gb": 10.2})()
            status = daemon.run_once()
        self.assertEqual(status.notes, "RAM critical at 10.2 GB; monitor will not start.")
        write_status_mock.assert_called_once()

    def test_detect_opencode_active_when_process_present(self):
        fake_proc = type("Proc", (), {"info": {"name": "opencode", "cmdline": ["opencode"]}})()
        with patch("orbits.daemon.monitor.psutil.process_iter", return_value=[fake_proc]):
            state = monitor.detect_opencode()
        self.assertEqual(state.status, "active")

    def test_parse_valid_usage_jsonl(self):
        summary = monitor.summarize_opencode_events([
            '{"usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":4}}',
            '{"usage":{"input_tokens":3,"cached_input_tokens":1,"output_tokens":5}}',
        ])
        self.assertEqual(summary.status, "active")
        self.assertEqual(summary.telemetry, "jsonl")
        self.assertEqual(summary.input_tokens, 13)
        self.assertEqual(summary.cached_input_tokens, 3)
        self.assertEqual(summary.output_tokens, 9)

    def test_ignore_standard_logs_and_estimate_tokens(self):
        summary = monitor.summarize_opencode_events(["starting agent", "plain text output"])
        self.assertEqual(summary.status, "active")
        self.assertEqual(summary.telemetry, "estimated")
        self.assertGreater(summary.input_tokens, 0)

    def test_fallback_on_empty_stream(self):
        summary = monitor.summarize_opencode_events([])
        self.assertEqual(summary.status, "unknown")
        self.assertEqual(summary.telemetry, "none")

    def test_error_detection(self):
        summary = monitor.summarize_opencode_events(['{"error":"boom"}'])
        self.assertEqual(summary.status, "error")
        self.assertEqual(summary.telemetry, "error")


if __name__ == "__main__":
    unittest.main()
