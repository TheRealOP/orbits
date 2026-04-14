import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _FakeProc:
    def __init__(self, pid, name, rss_mb, cmdline=None):
        self.info = {
            "pid": pid,
            "name": name,
            "cmdline": cmdline or [name],
            "memory_info": SimpleNamespace(rss=int(rss_mb * 1024 * 1024)),
        }


class _LiveProc:
    def __init__(self, pid):
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class TestRamManager(unittest.TestCase):
    def setUp(self):
        if "orchestration.ram_manager" in sys.modules:
            del sys.modules["orchestration.ram_manager"]
        self.mod = importlib.import_module("orchestration.ram_manager")

    def _vm(self, used_gb):
        used = int(used_gb * 1024**3)
        total = int(16 * 1024**3)
        available = max(0, total - used)
        return SimpleNamespace(used=used, total=total, available=available)

    def test_classifies_safe_warning_and_critical(self):
        self.assertEqual(self.mod.classify_total_used_bytes(int(7.5 * 1024**3)), "safe")
        self.assertEqual(self.mod.classify_total_used_bytes(int(8.5 * 1024**3)), "warning")
        self.assertEqual(self.mod.classify_total_used_bytes(int(10 * 1024**3)), "critical")

    def test_collect_snapshot_orders_top_processes(self):
        fake_procs = [
            _FakeProc(1, "small", 100),
            _FakeProc(2, "large", 900),
            _FakeProc(3, "mid", 400),
        ]
        with patch("orchestration.ram_manager.psutil.virtual_memory", return_value=self._vm(7.0)), patch(
            "orchestration.ram_manager.psutil.process_iter", return_value=fake_procs
        ):
            snapshot = self.mod.collect_snapshot()
        self.assertEqual(snapshot.state, "safe")
        self.assertEqual([proc.pid for proc in snapshot.top_processes[:3]], [2, 3, 1])

    def test_gate_blocks_non_essential_launches_in_warning(self):
        with patch("orchestration.ram_manager.psutil.virtual_memory", return_value=self._vm(8.2)), patch(
            "orchestration.ram_manager.psutil.process_iter", return_value=[]
        ):
            snapshot = self.mod.collect_snapshot()
        decision = self.mod.gate_launch(essential=False, snapshot=snapshot)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "warning")

    def test_gate_allows_essential_launch_in_warning(self):
        with patch("orchestration.ram_manager.psutil.virtual_memory", return_value=self._vm(8.2)), patch(
            "orchestration.ram_manager.psutil.process_iter", return_value=[]
        ):
            snapshot = self.mod.collect_snapshot()
        decision = self.mod.gate_launch(essential=True, snapshot=snapshot)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.state, "warning")

    def test_gate_blocks_everything_in_critical(self):
        with patch("orchestration.ram_manager.psutil.virtual_memory", return_value=self._vm(10.4)), patch(
            "orchestration.ram_manager.psutil.process_iter", return_value=[]
        ):
            snapshot = self.mod.collect_snapshot()
        decision = self.mod.gate_launch(essential=True, snapshot=snapshot)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "critical")

    def test_enforcement_reports_failure_when_ram_stays_critical(self):
        # top_processes must be ProcessInfo objects (not _FakeProc), since
        # _matches_non_essential_process accesses .name and .cmdline directly.
        candidates = [self.mod.ProcessInfo(pid=12, name="python", rss_mb=1500.0, cmdline="embedding_worker")]
        live_proc = _LiveProc(12)
        with patch("orchestration.ram_manager.collect_snapshot") as collect_snapshot, patch(
            "orchestration.ram_manager.psutil.Process", return_value=live_proc
        ), patch("orchestration.ram_manager.psutil.wait_procs", return_value=([], [live_proc])):
            collect_snapshot.side_effect = [
                self.mod.RamSnapshot(int(10.5 * 1024**3), 10.5, 0, int(16 * 1024**3), "critical", candidates, captured_at="now"),
                self.mod.RamSnapshot(int(8.4 * 1024**3), 8.4, 0, int(16 * 1024**3), "warning", [], captured_at="later"),
            ]
            result = self.mod.enforce_limits()
        self.assertTrue(result.attempted)
        self.assertFalse(result.recovered)
        self.assertEqual(result.final_state, "warning")
        self.assertIn("safe threshold", result.failure)
        self.assertTrue(live_proc.terminated)
        self.assertTrue(live_proc.killed)

    def test_write_state_creates_json_file(self):
        snapshot = self.mod.RamSnapshot(int(7 * 1024**3), 7.0, 0, int(16 * 1024**3), "safe", [], captured_at="now")
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ram_state.json"
            self.mod.write_state(snapshot, target)
            self.assertTrue(target.exists())
            self.assertIn('"state": "safe"', target.read_text())


if __name__ == "__main__":
    unittest.main()
