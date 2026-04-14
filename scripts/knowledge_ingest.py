#!/usr/bin/env python3
"""
knowledge_ingest.py — walk Knowledge/notes/, distil each note, store in slm.

- Maintains a manifest at Knowledge/ingested/manifest.json keyed by file path.
- Only ingests new or SHA-changed files (idempotent).
- Audit log per day at Knowledge/ingested/YYYY-MM-DD.jsonl.

Usage:
    python scripts/knowledge_ingest.py                  # ingest all new/changed
    python scripts/knowledge_ingest.py --force          # re-ingest everything
    python scripts/knowledge_ingest.py path/to/note.md  # ingest one file
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT     = Path(__file__).parent.parent
NOTES_DIR     = REPO_ROOT / "Knowledge" / "notes"
INGESTED_DIR  = REPO_ROOT / "Knowledge" / "ingested"
MANIFEST_PATH = INGESTED_DIR / "manifest.json"

sys.path.insert(0, str(REPO_ROOT))

import orchestration.memory as memory
from orchestration.brain.distiller import distill


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_manifest(manifest: dict) -> None:
    INGESTED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def audit(entry: dict) -> None:
    INGESTED_DIR.mkdir(parents=True, exist_ok=True)
    log = INGESTED_DIR / (datetime.date.today().isoformat() + ".jsonl")
    with log.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def ingest_file(filepath: Path, manifest: dict, force: bool = False) -> str:
    """Return 'ok', 'skip', or 'fail'."""
    filepath = filepath if filepath.is_absolute() else (REPO_ROOT / filepath)
    raw = filepath.read_text(encoding="utf-8", errors="replace")
    file_sha = sha256(raw)
    key = str(filepath.relative_to(REPO_ROOT))

    if not force and manifest.get(key) == file_sha:
        print(f"  skip  {filepath.name}  (unchanged)")
        return "skip"

    print(f"  ingest {filepath.name} ...", end=" ", flush=True)
    distilled, metadata = distill(str(filepath), raw)
    metadata.setdefault("source_path", key)
    metadata.setdefault("sha", file_sha)

    ok = memory.remember(distilled, metadata=metadata)
    if ok:
        manifest[key] = file_sha
        audit({
            "ts":     datetime.datetime.now(datetime.UTC).isoformat(),
            "file":   key,
            "sha":    file_sha,
            "topic":  metadata.get("topic", ""),
            "slug":   metadata.get("slug", ""),
            "tags":   metadata.get("tags", []),
            "chars":  len(distilled),
        })
        print("ok")
        return "ok"
    else:
        print("FAILED (slm unavailable or timeout?)")
        return "fail"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Knowledge/notes/ into slm")
    parser.add_argument("files",   nargs="*", help="Specific files to ingest")
    parser.add_argument("--force", action="store_true", help="Re-ingest unchanged files")
    args = parser.parse_args()

    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    INGESTED_DIR.mkdir(parents=True, exist_ok=True)

    if args.files:
        targets = [Path(f) for f in args.files]
    else:
        targets = sorted(NOTES_DIR.glob("**/*.md"))

    if not targets:
        print("No .md files found in Knowledge/notes/")
        print("Drop some markdown notes there and re-run.")
        sys.exit(0)

    manifest = load_manifest()
    ingested = skipped = failed = 0

    print(f"\nInspecting {len(targets)} file(s)...\n")
    for filepath in targets:
        try:
            status = ingest_file(filepath, manifest, force=args.force)
            if status == "ok":
                ingested += 1
            elif status == "skip":
                skipped += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  error  {filepath.name}: {e}")
            failed += 1

    save_manifest(manifest)

    print(f"\n{'─'*40}")
    print(f"  Ingested: {ingested}")
    print(f"  Skipped:  {skipped}  (unchanged)")
    print(f"  Failed:   {failed}")
    print(f"{'─'*40}")

    if ingested > 0:
        print("\nRun /knowledge-sync or:")
        print("  python -m orchestration.brain.linker")
        print("to cross-link the new notes.\n")


if __name__ == "__main__":
    main()
