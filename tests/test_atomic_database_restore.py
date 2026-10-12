"""Failure-injection tests for offline atomic, manifest-verified DB recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from bridge.infrastructure.database.backup import BackupManifest, BackupService


class AtomicDatabaseRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / "verified.db"
        self.destination = self.root / "active.db"
        self._make_db(self.backup, "backed-up")
        self._make_db(self.destination, "original")
        payload = self.backup.read_bytes()
        self.manifest = BackupManifest(
            created_at="2026-10-13T00:00:00+00:00",
            database_sha256=hashlib.sha256(payload).hexdigest(),
            database_bytes=len(payload),
            rpo_seconds=3600,
            rto_seconds=900,
        )
        self.manifest_path = self.backup.with_suffix(".db.manifest.json")
        self.manifest_path.write_text(json.dumps(asdict(self.manifest)), encoding="utf-8")

    @staticmethod
    def _make_db(path: Path, value: str) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE data (value TEXT NOT NULL)")
            connection.execute("INSERT INTO data VALUES (?)", (value,))

    def _value(self) -> str:
        with sqlite3.connect(self.destination) as connection:
            return connection.execute("SELECT value FROM data").fetchone()[0]

    def _assert_no_temp_files(self) -> None:
        self.assertEqual(list(self.root.glob(".restore-*.db")), [])

    def test_restores_verified_snapshot_over_existing_offline_database(self) -> None:
        BackupService.restore(self.backup, self.destination)
        self.assertEqual(self._value(), "backed-up")
        self._assert_no_temp_files()

    def test_corrupted_backup_keeps_existing_database_untouched(self) -> None:
        self.backup.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            BackupService.restore(self.backup, self.destination)
        self.assertEqual(self._value(), "original")
        self._assert_no_temp_files()

    def test_missing_or_modified_manifest_refuses_restore(self) -> None:
        self.manifest_path.unlink()
        with self.assertRaises(ValueError):
            BackupService.restore(self.backup, self.destination)
        self.assertEqual(self._value(), "original")
        self.manifest_path.write_text(json.dumps({
            **asdict(self.manifest), "database_sha256": "0" * 64,
        }))
        with self.assertRaises(ValueError):
            BackupService.restore(self.backup, self.destination)
        self.assertEqual(self._value(), "original")

    def test_stale_wal_sidecars_block_database_activation(self) -> None:
        wal = self.destination.with_name(self.destination.name + "-wal")
        wal.write_bytes(b"stale")
        with self.assertRaisesRegex(ValueError, "WAL"):
            BackupService.restore(self.backup, self.destination)
        self.assertEqual(self._value(), "original")
        wal.unlink()

    def test_rename_failure_preserves_previous_database_and_cleans_stage(self) -> None:
        with patch("bridge.infrastructure.database.backup.os.replace",
                   side_effect=OSError("simulated interrupted activation")):
            with self.assertRaises(OSError):
                BackupService.restore(self.backup, self.destination)
        self.assertEqual(self._value(), "original")
        self._assert_no_temp_files()

    def test_refuse_self_restore_and_symlink_destination(self) -> None:
        with self.assertRaises(ValueError):
            BackupService.restore(self.backup, self.backup)
        link = self.root / "link.db"
        try:
            link.symlink_to(self.destination)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        with self.assertRaises(ValueError):
            BackupService.restore(self.backup, link)
        self.assertEqual(self._value(), "original")


if __name__ == "__main__":
    unittest.main()
