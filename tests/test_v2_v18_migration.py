import sqlite3
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.v18_migration import V18MigrationPlanner
from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase


class V18MigrationTests(unittest.IsolatedAsyncioTestCase):
    def _legacy(self, path: Path) -> None:
        db = sqlite3.connect(str(path))
        db.executescript("""
        CREATE TABLE sessions(telegram_user_id TEXT PRIMARY KEY, opencode_session_id TEXT);
        INSERT INTO sessions VALUES('42','s1');
        CREATE TABLE scheduled_jobs(id INTEGER PRIMARY KEY, owner_id TEXT, chat_id INTEGER, name TEXT, prompt TEXT, enabled INTEGER, created_at TEXT, updated_at TEXT, repeat_seconds INTEGER);
        INSERT INTO scheduled_jobs VALUES(7,'42',99,'daily','run',1,'c','u',3600);
        CREATE TABLE pending_attachment_batches(owner_id TEXT, chat_id INTEGER, attachments_json TEXT, created_at TEXT, updated_at TEXT, expires_at TEXT);
        INSERT INTO pending_attachment_batches VALUES('42',99,'[{"file_id":"a"},{"file_id":"b"}]','c','u','e');
        """)
        db.commit()
        db.close()

    def test_backup_first_dry_run_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "v18.db"
            backup = Path(tmp) / "v18.backup.db"
            self._legacy(source)
            planner = V18MigrationPlanner(source, backup)
            with self.assertRaises(RuntimeError):
                planner.inspect(dry_run=True)
            planner.create_backup()
            report = planner.inspect(dry_run=True)
            self.assertEqual(report.sessions, 1)
            self.assertEqual(report.schedules, 1)
            self.assertEqual(report.pending_attachments, 2)
            self.assertEqual(report.owners, {"42"})
            self.assertTrue(report.dry_run)

    def test_schedule_migration_key_is_stable_and_occurrence_safe(self):
        a = V18MigrationPlanner.schedule_key("42", 7)
        b = V18MigrationPlanner.schedule_key("42", 7)
        c = V18MigrationPlanner.schedule_key("42", 8)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_backup_is_rollback_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "v18.db"
            backup = Path(tmp) / "v18.backup.db"
            self._legacy(source)
            planner = V18MigrationPlanner(source, backup)
            planner.create_backup()
            db = sqlite3.connect(str(source))
            db.execute("DELETE FROM sessions")
            db.commit()
            db.close()
            restored = sqlite3.connect(str(backup))
            try:
                self.assertEqual(restored.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
            finally:
                restored.close()


    async def test_apply_is_idempotent_for_sessions_schedules_and_pending_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, backup, target = root / "v18.db", root / "backup.db", root / "v2.db"
            self._legacy(source)
            planner = V18MigrationPlanner(source, backup)
            planner.create_backup()
            database = BridgeDatabase(target)
            await MigrationRunner(database).migrate()
            await database.close()
            planner.migrate_into(target)
            planner.migrate_into(target)
            db = sqlite3.connect(str(target))
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_sessions WHERE owner_id='42'").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM schedules WHERE owner_id='42' AND name='daily'").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM pending_attachment_batches WHERE owner_id='42'").fetchone()[0], 1)
            finally:
                db.close()
