import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.migrations import MIGRATIONS, MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase


class MigrationTests(unittest.TestCase):
    def test_migrations_create_v2_tables_and_are_idempotent(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                database = BridgeDatabase(Path(tmp) / "bridge.db")
                runner = MigrationRunner(database)
                self.assertEqual(await runner.migrate(), [migration.version for migration in MIGRATIONS])
                self.assertEqual(await runner.migrate(), [])
                db = await database.connect()
                async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
                    tables = {row[0] for row in await cursor.fetchall()}
                expected = {
                    "schema_migrations", "task_attempts", "task_events", "attachments",
                    "task_outputs", "schedules", "schedule_runs", "agent_sessions",
                    "user_settings", "resource_snapshots", "failure_records",
                }
                self.assertTrue(expected.issubset(tables))
                async with db.execute("PRAGMA foreign_keys") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 1)
                self.assertEqual(await database.integrity_check(), "ok")
                await database.close()
        asyncio.run(scenario())

    def test_failed_migration_transaction_rolls_back(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                database = BridgeDatabase(Path(tmp) / "bridge.db")
                db = await database.connect()
                with self.assertRaises(sqlite3.OperationalError):
                    async with database.transaction(immediate=True):
                        await db.execute("CREATE TABLE rollback_probe(id INTEGER PRIMARY KEY)")
                        await db.execute("INSERT INTO missing_table VALUES (1)")
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='rollback_probe'"
                ) as cursor:
                    self.assertIsNone(await cursor.fetchone())
                await database.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
