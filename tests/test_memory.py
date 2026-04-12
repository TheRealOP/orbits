"""
test_memory.py — unit tests for orchestration/memory.py.

Uses monkeypatched subprocess.run — no real slm binary required.
"""
import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _make_proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestMemoryAvailability(unittest.TestCase):
    def setUp(self):
        # Force fresh import so cached _SLM_AVAILABLE is reset
        if "orchestration.memory" in sys.modules:
            del sys.modules["orchestration.memory"]

    def test_available_when_status_returns_0(self):
        with patch("subprocess.run", return_value=_make_proc(0)):
            import orchestration.memory as mem
            self.assertTrue(mem._check_available())

    def test_unavailable_when_status_fails(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            import orchestration.memory as mem
            self.assertFalse(mem._check_available())

    def test_unavailable_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("slm", 5)):
            import orchestration.memory as mem
            self.assertFalse(mem._check_available())


class TestRemember(unittest.TestCase):
    def setUp(self):
        if "orchestration.memory" in sys.modules:
            del sys.modules["orchestration.memory"]

    def test_remember_returns_true_on_success(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _make_proc(0)

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.memory as mem
            result = mem.remember("hello world")
        self.assertTrue(result)

    def test_remember_embeds_metadata_prefix(self):
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return _make_proc(0)

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.memory as mem
            mem.remember("some fact", metadata={"topic": "test", "slug": "test_slug", "tags": ["a", "b"]})

        # The 'remember' call (index 1, after status check) should embed metadata
        remember_call = [c for c in captured if "remember" in c]
        self.assertTrue(any("[topic: test]" in arg for call in remember_call for arg in call))

    def test_remember_returns_false_when_unavailable(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            import orchestration.memory as mem
            result = mem.remember("test")
        self.assertFalse(result)

    def test_remember_returns_false_on_nonzero_exit(self):
        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:   # status check
                return _make_proc(0)
            return _make_proc(1)     # remember fails

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.memory as mem
            result = mem.remember("test")
        self.assertFalse(result)


class TestRecall(unittest.TestCase):
    def setUp(self):
        if "orchestration.memory" in sys.modules:
            del sys.modules["orchestration.memory"]

    def _slm_v3_response(self, results):
        import json
        return json.dumps({"data": {"results": results}})

    def test_recall_returns_normalised_results(self):
        raw = self._slm_v3_response([
            {"content": "fact one", "score": 0.9},
            {"content": "fact two", "score": 0.7},
        ])

        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_proc(0)          # status
            return _make_proc(0, stdout=raw)  # recall

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.memory as mem
            results = mem.recall("test query", k=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["text"], "fact one")
        self.assertAlmostEqual(results[0]["score"], 0.9)

    def test_recall_respects_k_limit(self):
        raw = self._slm_v3_response([
            {"content": f"fact {i}", "score": 0.9 - i * 0.1}
            for i in range(10)
        ])

        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_proc(0)
            return _make_proc(0, stdout=raw)

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.memory as mem
            results = mem.recall("q", k=3)

        self.assertEqual(len(results), 3)

    def test_recall_returns_empty_when_unavailable(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            import orchestration.memory as mem
            results = mem.recall("q")
        self.assertEqual(results, [])

    def test_recall_returns_empty_on_bad_json(self):
        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_proc(0)
            return _make_proc(0, stdout="not json")

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.memory as mem
            results = mem.recall("q")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
