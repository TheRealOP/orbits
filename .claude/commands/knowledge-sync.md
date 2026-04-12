Ingest new or changed markdown files from Knowledge/notes/ into SuperLocalMemory, then cross-link them.

Usage: /knowledge-sync

Run this shell command and show the output:
```bash
cd "$(git rev-parse --show-toplevel)" && \
  .venv/bin/python scripts/knowledge_ingest.py && \
  .venv/bin/python -m orchestration.brain.linker
```

If .venv/bin/python is not found, run `bash scripts/bootstrap.sh` first.
