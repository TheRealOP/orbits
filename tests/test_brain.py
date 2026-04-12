"""
test_brain.py — unit tests for orchestration/brain modules.

Patches both subprocess.run (for gemini + slm) and orchestration.gemini.ask/ask_json
at the module level so each brain module can be tested in isolation.
"""
import json
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


class TestCurator(unittest.TestCase):
    def setUp(self):
        for mod in list(sys.modules):
            if "orchestration" in mod:
                del sys.modules[mod]
        os.environ.pop("ORBITS_NO_AUTO_REMEMBER", None)

    def test_skips_read_only_tools(self):
        with patch("subprocess.run", return_value=_make_proc(0)):
            import orchestration.brain.curator as curator
            ok, meta = curator.should_remember("Read", "x" * 200)
        self.assertFalse(ok)

    def test_skips_short_outputs(self):
        with patch("subprocess.run", return_value=_make_proc(0)):
            import orchestration.brain.curator as curator
            ok, _ = curator.should_remember("Bash", "short")
        self.assertFalse(ok)

    def test_skips_boilerplate(self):
        with patch("subprocess.run", return_value=_make_proc(0)):
            import orchestration.brain.curator as curator
            ok, _ = curator.should_remember("Write", "File created successfully")
        self.assertFalse(ok)

    def test_remembers_when_gemini_says_yes(self):
        gemini_resp = json.dumps({
            "remember": True,
            "reason": "important decision",
            "topic": "arch decision",
            "slug": "arch_decision",
            "tags": ["project/orbits"],
        })

        # Curator calls gemini directly (no slm status check); return response immediately.
        with patch("subprocess.run", return_value=_make_proc(0, stdout=gemini_resp)):
            import orchestration.brain.curator as curator
            ok, meta = curator.should_remember("Bash", "x" * 200)

        self.assertTrue(ok)
        self.assertEqual(meta["topic"], "arch decision")

    def test_kill_switch(self):
        os.environ["ORBITS_NO_AUTO_REMEMBER"] = "1"
        with patch("subprocess.run", return_value=_make_proc(0)):
            import orchestration.brain.curator as curator
            ok, _ = curator.should_remember("Bash", "x" * 200)
        self.assertFalse(ok)


class TestSynthesizer(unittest.TestCase):
    def setUp(self):
        for mod in list(sys.modules):
            if "orchestration" in mod:
                del sys.modules[mod]
        os.environ["GEMINI_DISABLED"] = "1"  # use fast path / raw fallback

    def test_returns_empty_for_no_chunks(self):
        import orchestration.brain.synthesizer as synth
        result = synth.synthesize("q", [])
        self.assertEqual(result, "")

    def test_fast_path_for_small_high_score_chunks(self):
        chunks = [
            {"text": "small fact", "score": 0.95},
        ]
        import orchestration.brain.synthesizer as synth
        result = synth.synthesize("q", chunks)
        self.assertIn("<memory>", result)
        self.assertIn("small fact", result)

    def test_raw_fallback_when_gemini_disabled(self):
        # More than fast-path threshold → would normally call Gemini, but it's disabled
        chunks = [{"text": f"chunk {i} " * 50, "score": 0.5} for i in range(5)]
        import orchestration.brain.synthesizer as synth
        result = synth.synthesize("q", chunks)
        self.assertIn("<memory>", result)
        self.assertIn("chunk 0", result)


class TestDistiller(unittest.TestCase):
    def setUp(self):
        for mod in list(sys.modules):
            if "orchestration" in mod:
                del sys.modules[mod]
        os.environ["GEMINI_DISABLED"] = "1"

    def test_falls_back_to_raw_when_gemini_disabled(self):
        import orchestration.brain.distiller as distiller
        text = "This is a test note about something important."
        distilled, metadata = distiller.distill("/fake/path/test.md", text)
        # With GEMINI_DISABLED, distilled should equal raw text
        self.assertEqual(distilled, text)
        # Metadata should still have fallback values
        self.assertIn("topic", metadata)
        self.assertIn("slug", metadata)
        self.assertIn("tags", metadata)


class TestTagger(unittest.TestCase):
    def setUp(self):
        for mod in list(sys.modules):
            if "orchestration" in mod:
                del sys.modules[mod]

    def test_graceful_degradation_when_gemini_unavailable(self):
        os.environ["GEMINI_DISABLED"] = "1"
        import orchestration.brain.tagger as tagger
        result = tagger.tag("hello world this is a test sentence")
        self.assertIn("topic", result)
        self.assertIn("slug", result)
        self.assertIn("tags", result)
        self.assertIsInstance(result["tags"], list)

    def test_uses_gemini_result_when_available(self):
        os.environ.pop("GEMINI_DISABLED", None)
        gemini_resp = json.dumps({
            "topic": "AI memory systems",
            "slug":  "ai_memory_systems",
            "tags":  ["ai", "memory"],
        })

        def fake_run(cmd, **kwargs):
            return _make_proc(0, stdout=gemini_resp)

        with patch("subprocess.run", side_effect=fake_run):
            import orchestration.brain.tagger as tagger
            result = tagger.tag("some text about ai memory systems")

        self.assertEqual(result["topic"], "AI memory systems")
        self.assertEqual(result["slug"],  "ai_memory_systems")
        self.assertIn("ai", result["tags"])


if __name__ == "__main__":
    unittest.main()
