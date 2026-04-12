"""
test_gemini.py — unit tests for orchestration/gemini.py.

Uses monkeypatched subprocess.run — no real gemini CLI required.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


def _make_proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestAsk(unittest.TestCase):
    def setUp(self):
        if "orchestration.gemini" in sys.modules:
            del sys.modules["orchestration.gemini"]
        os.environ.pop("GEMINI_DISABLED", None)

    def test_returns_output_on_success(self):
        with patch("subprocess.run", return_value=_make_proc(0, stdout="hello")):
            import orchestration.gemini as gem
            result = gem.ask("test prompt")
        self.assertEqual(result, "hello")

    def test_cascade_fallthrough_on_error(self):
        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:                # first two tiers fail
                return _make_proc(1)
            return _make_proc(0, stdout="ok")    # third tier succeeds

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.gemini as gem
            result = gem.ask("test", _cascade=gem.MODEL_CASCADE)

        self.assertEqual(result, "ok")
        self.assertEqual(call_count[0], 3)

    def test_returns_none_when_all_tiers_fail(self):
        with patch("subprocess.run", return_value=_make_proc(1)):
            import orchestration.gemini as gem
            result = gem.ask("test")
        self.assertIsNone(result)

    def test_returns_none_when_binary_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            import orchestration.gemini as gem
            result = gem.ask("test")
        self.assertIsNone(result)

    def test_gemini_disabled_env(self):
        os.environ["GEMINI_DISABLED"] = "1"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _make_proc(0, stdout="should not reach")

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.gemini as gem
            result = gem.ask("test")

        self.assertIsNone(result)
        self.assertEqual(calls, [])  # subprocess never called

    def test_strips_trailing_whitespace(self):
        with patch("subprocess.run", return_value=_make_proc(0, stdout="  result  \n")):
            import orchestration.gemini as gem
            result = gem.ask("test")
        self.assertEqual(result, "result")


class TestAskJson(unittest.TestCase):
    def setUp(self):
        if "orchestration.gemini" in sys.modules:
            del sys.modules["orchestration.gemini"]
        os.environ.pop("GEMINI_DISABLED", None)

    def test_parses_json_response(self):
        with patch("subprocess.run", return_value=_make_proc(0, stdout='{"key": "val"}')):
            import orchestration.gemini as gem
            result = gem.ask_json("test")
        self.assertEqual(result, {"key": "val"})

    def test_strips_markdown_fences(self):
        raw = "```json\n{\"key\": \"val\"}\n```"
        with patch("subprocess.run", return_value=_make_proc(0, stdout=raw)):
            import orchestration.gemini as gem
            result = gem.ask_json("test")
        self.assertEqual(result, {"key": "val"})

    def test_returns_none_on_invalid_json(self):
        with patch("subprocess.run", return_value=_make_proc(0, stdout="not json")):
            import orchestration.gemini as gem
            result = gem.ask_json("test")
        self.assertIsNone(result)

    def test_returns_none_when_ask_fails(self):
        with patch("subprocess.run", return_value=_make_proc(1)):
            import orchestration.gemini as gem
            result = gem.ask_json("test")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
