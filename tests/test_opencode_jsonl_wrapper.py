import json
from pathlib import Path
import tempfile
import unittest

from scripts.opencode_jsonl_wrapper import process_stream_line, run_and_capture


class _Sink:
    def __init__(self):
        self.lines = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        return None


class TestOpenCodeJsonlWrapper(unittest.TestCase):
    def test_process_stream_line_keeps_only_valid_json(self):
        sink = _Sink()
        process_stream_line('{"usage":{"input_tokens":1}}\n', sink)
        process_stream_line('plain text\n', sink)
        stored = [line for line in sink.lines if line.strip().startswith('{')]
        self.assertEqual(len(stored), 1)
        self.assertEqual(json.loads(stored[0])["usage"]["input_tokens"], 1)

    def test_run_and_capture_writes_jsonl_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "agent1.jsonl"
            code = "import sys; print('plain'); print('{\\\"usage\\\":{\\\"input_tokens\\\":2}}')"
            rc = run_and_capture(output_path, ["python3", "-c", code])
            self.assertEqual(rc, 0)
            lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["usage"]["input_tokens"], 2)


if __name__ == "__main__":
    unittest.main()
