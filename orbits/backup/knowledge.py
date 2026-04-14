"""Knowledge directory backup helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orbits.daemon.monitor import load_config


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else REPO_ROOT / path


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def backup_config(config: dict | None = None) -> dict:
    config = config or load_config()
    backup = config.get("backup", {})
    return {
        "source_dir": str(_resolve(backup.get("source_dir", "Knowledge"))),
        "backup_dir": str(_resolve(backup.get("backup_dir", "orbits/state/backups"))),
        "manifest_file": str(_resolve(backup.get("manifest_file", "orbits/state/backups/knowledge_manifest.json"))),
        "retention_count": int(backup.get("retention_count", 5)),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_manifest(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def _iter_backup_sources(source_dir: Path):
    for path in source_dir.iterdir() if source_dir.exists() else []:
        if path.is_file():
            yield path

    preferred_dirs = [
        source_dir / "notes",
        source_dir / "ingested",
        source_dir / "progress",
        source_dir / "logs",
        source_dir / "zFinantial_stuff",
    ]
    for directory in preferred_dirs:
        if directory.exists():
            yield directory

    slm_dir = source_dir / "slm_data"
    if slm_dir.exists():
        for name in ["memory.db", "pending.db", "audit_chain.db", "config.json", "code_graph_config.json", ".setup-complete", ".last-consolidation"]:
            path = slm_dir / name
            if path.exists():
                yield path


def _snapshot_sqlite(path: Path, tmp_dir: Path) -> Path:
    snapshot = tmp_dir / path.name
    source = sqlite3.connect(path)
    dest = sqlite3.connect(snapshot)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return snapshot


def create_backup(config: dict | None = None) -> dict:
    cfg = backup_config(config)
    source_dir = Path(cfg["source_dir"])
    backup_dir = Path(cfg["backup_dir"])
    manifest_path = Path(cfg["manifest_file"])
    backup_dir.mkdir(parents=True, exist_ok=True)

    archive_path = backup_dir / f"knowledge-{_utc_stamp()}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        with tarfile.open(archive_path, "w:gz") as archive:
            for path in _iter_backup_sources(source_dir):
                archive_path_name = path.relative_to(source_dir.parent)
                if path.suffix == ".db":
                    snap = _snapshot_sqlite(path, tmp_dir)
                    archive.add(snap, arcname=str(archive_path_name), recursive=False)
                else:
                    archive.add(path, arcname=str(archive_path_name), recursive=path.is_dir())

    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": _display_path(archive_path),
        "size_bytes": archive_path.stat().st_size,
        "sha256": _sha256(archive_path),
    }

    manifest = _load_manifest(manifest_path)
    manifest.append(entry)

    retention = cfg["retention_count"]
    while len(manifest) > retention:
        removed = manifest.pop(0)
        old_archive = Path(removed["archive"])
        if not old_archive.is_absolute():
            old_archive = REPO_ROOT / old_archive
        if old_archive.exists():
            old_archive.unlink()

    _write_manifest(manifest_path, manifest)
    return entry


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Create a Knowledge backup archive")


def main() -> int:
    build_parser().parse_args()
    result = create_backup()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
