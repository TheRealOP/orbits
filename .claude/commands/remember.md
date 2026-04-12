Store text in your personal memory (SuperLocalMemory) with auto-generated metadata.

Usage: /remember <text to store>

Run this shell command:
```bash
cd "$(git rev-parse --show-toplevel)" && \
  .venv/bin/python -c "
import sys
sys.path.insert(0, '.')
import orchestration.memory as m
from orchestration.brain.tagger import tag
text = '''$ARGUMENTS'''
metadata = tag(text)
ok = m.remember(text, metadata=metadata)
print('Stored:', metadata['topic'] if ok else 'FAILED — is slm running? Run: bash scripts/bootstrap.sh')
"
```
