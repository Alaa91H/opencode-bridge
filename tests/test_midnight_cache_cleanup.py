"""UTC boundary and recovery guards for the optional cache-cleanup timer."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridge.services.download_service import DownloadBusy, DownloadRecord, DownloadService

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "expire-midnight-downloads.py"
SPEC = importlib.util.spec_from_file_location("midnight_cleanup_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class MidnightCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.root = self.project / "runtime" / "downloads"
        self.service = DownloadService(self.root)
        self.service.ensure_directories()
        self.database = self.project / "sessions.db"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE agent_tasks (status TEXT)")
        self.midnight = datetime(2026, 10, 13, 0, 5, tzinfo=UTC)

    def record(self, *, yesterday: bool) -> DownloadRecord:
        index = self.service._read_index()
        rid = ("a" if yesterday else "b") * 32
        file = self.root / (rid + ".bin")
        file.write_bytes(b"cache-data")
        created = (self.midnight - timedelta(days=1) if yesterday
                   else self.midnight)
        record = DownloadRecord(
            id=rid, owner_id="42", filename=file.name, path=str(file),
            size_bytes=file.stat().st_size, mime="application/octet-stream",
            source_url="https://example.org/download",
            created_at=created.isoformat(),
            expires_at=(self.midnight + timedelta(hours=12)).isoformat(),
            direct_send_limit=1000,
        )
        index[record.id] = record.to_dict()
        self.service._write_index(index)
        return record

    def test_purges_prior_day_only_at_utc_midnight(self) -> None:
        old = self.record(yesterday=True)
        new = self.record(yesterday=False)
        result = module.midnight_cleanup(self.project, self.midnight)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["removed"], 1)
        self.assertFalse(Path(old.path).exists())
        self.assertTrue(Path(new.path).exists())

    def test_running_download_process_blocks_midnight_cleanup(self) -> None:
        old = self.record(yesterday=True)
        with self.service._storage_guard():
            with self.assertRaises(DownloadBusy):
                module.midnight_cleanup(self.project, self.midnight)
        self.assertTrue(Path(old.path).is_file())

    def test_skips_if_queue_is_busy(self) -> None:
        old = self.record(yesterday=True)
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO agent_tasks VALUES ('running')")
        result = module.midnight_cleanup(self.project, self.midnight)
        self.assertEqual(result["status"], "skipped")
        self.assertTrue(Path(old.path).exists())

    def test_skips_active_transfer_workfiles(self) -> None:
        old = self.record(yesterday=True)
        unfinished = self.root / "42" / "still.part"
        unfinished.parent.mkdir()
        unfinished.write_bytes(b"downloading")
        result = module.midnight_cleanup(self.project, self.midnight)
        self.assertEqual(result["status"], "skipped")
        self.assertTrue(Path(old.path).exists())

    def test_emergency_stop_blocks_midnight_cache_deletion(self) -> None:
        old = self.record(yesterday=True)
        (self.project / "runtime" / "autopilot-mode").write_text(
            "emergency-stop\n", encoding="ascii",
        )
        result = module.midnight_cleanup(self.project, self.midnight)
        self.assertEqual(result["status"], "skipped")
        self.assertTrue(Path(old.path).is_file())

    def test_outside_midnight_window_is_read_only(self) -> None:
        old = self.record(yesterday=True)
        for hour in (1, 12, 23):
            with self.subTest(hour=hour):
                result = module.midnight_cleanup(
                    self.project, self.midnight.replace(hour=hour),
                )
                self.assertEqual(result["status"], "skipped")
                self.assertTrue(Path(old.path).exists())

    def test_corrupted_sqlite_fails_without_deleting_cache(self) -> None:
        old = self.record(yesterday=True)
        self.database.write_bytes(b"corrupted test fixture")
        with self.assertRaises(sqlite3.DatabaseError):
            module.midnight_cleanup(self.project, self.midnight)
        self.assertTrue(Path(old.path).exists())

    def test_timer_is_utc_fixed_and_not_auto_enabled_by_service_install(self) -> None:
        timer = (PROJECT / "deploy" / "opencode-bridge-download-cleanup.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 00:00:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        installer = (PROJECT / "systemd.py").read_text(encoding="utf-8")
        self.assertNotIn("enable opencode-bridge-download-cleanup.timer", installer)


if __name__ == "__main__":
    unittest.main()
