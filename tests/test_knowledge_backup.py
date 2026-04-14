import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from orbits.backup.knowledge import create_backup


class TestKnowledgeBackup(unittest.TestCase):
    def _config(self, base: Path) -> dict:
        return {
            "backup": {
                "source_dir": str(base / "Knowledge"),
                "backup_dir": str(base / "orbits" / "state" / "backups"),
                "manifest_file": str(base / "orbits" / "state" / "backups" / "knowledge_manifest.json"),
                "retention_count": 2,
            }
        }

    def test_backup_archive_created_with_knowledge_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = base / "Knowledge"
            source.mkdir(parents=True)
            (source / "note.md").write_text("hello", encoding="utf-8")
            entry = create_backup(self._config(base))
            archive = base / entry["archive"]
            self.assertTrue(archive.exists())
            with tarfile.open(archive, "r:gz") as tar:
                self.assertIn("Knowledge/note.md", tar.getnames())

    def test_manifest_entry_is_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = base / "Knowledge"
            source.mkdir(parents=True)
            (source / "note.md").write_text("hello", encoding="utf-8")
            create_backup(self._config(base))
            manifest = json.loads((base / "orbits" / "state" / "backups" / "knowledge_manifest.json").read_text())
            self.assertEqual(len(manifest), 1)

    def test_retention_prunes_old_archives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = base / "Knowledge"
            source.mkdir(parents=True)
            for i in range(3):
                (source / f"note-{i}.md").write_text(str(i), encoding="utf-8")
                create_backup(self._config(base))
            manifest = json.loads((base / "orbits" / "state" / "backups" / "knowledge_manifest.json").read_text())
            archives = list((base / "orbits" / "state" / "backups").glob("knowledge-*.tar.gz"))
            self.assertEqual(len(manifest), 2)
            self.assertEqual(len(archives), 2)

    def test_backup_output_directory_is_excluded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source = base / "Knowledge"
            source.mkdir(parents=True)
            (source / "note.md").write_text("hello", encoding="utf-8")
            backup_root = base / "orbits" / "state" / "backups"
            backup_root.mkdir(parents=True)
            (backup_root / "old.tar.gz").write_text("ignore me", encoding="utf-8")
            entry = create_backup(self._config(base))
            archive = base / entry["archive"]
            with tarfile.open(archive, "r:gz") as tar:
                names = tar.getnames()
                self.assertNotIn("orbits/state/backups/old.tar.gz", names)


if __name__ == "__main__":
    unittest.main()
