Query your personal memory (SuperLocalMemory) and return the top relevant results, synthesised by Gemini.

Usage: /recall <your query>

Run this shell command and show the output to the user:
```bash
cd "$(git rev-parse --show-toplevel)" && \
  .venv/bin/python -m orchestration.recall_injector --query "$ARGUMENTS" --k 8
```

If .venv/bin/python is not found, inform the user to run `bash scripts/bootstrap.sh` first.
