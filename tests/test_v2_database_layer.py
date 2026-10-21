import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.sqlite import BridgeDatabase


class BridgeDatabaseTests(unittest.TestCase):
    def test_connection_pragmas_transactions_checkpoint_and_integrity(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "bridge.db"
                database = BridgeDatabase(path, busy_timeout_ms=3210)
                db = await database.connect()
                async with db.execute("PRAGMA journal_mode") as cursor:
                    self.assertEqual((await cursor.fetchone())[0].lower(), "wal")
                async with db.execute("PRAGMA foreign_keys") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 1)
                async with db.execute("PRAGMA busy_timeout") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 3210)

                async with database.transaction(immediate=True) as tx:
                    await tx.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
                    await tx.execute(
                        "CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id))"
                    )
                    await tx.execute("INSERT INTO parent(id) VALUES (1)")

                with self.assertRaises(sqlite3.IntegrityError):
                    async with database.transaction() as tx:
                        await tx.execute("INSERT INTO child(id, parent_id) VALUES (1, 999)")

                async with db.execute("SELECT COUNT(*) FROM child") as cursor:
                    self.assertEqual((await cursor.fetchone())[0], 0)
                self.assertEqual(await database.integrity_check(), "ok")
                checkpoint = await database.checkpoint()
                self.assertEqual(len(checkpoint), 3)
                await database.close()

        asyncio.run(scenario())

    def test_rejects_unknown_checkpoint_mode(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                database = BridgeDatabase(Path(tmp) / "bridge.db")
                with self.assertRaises(ValueError):
                    await database.checkpoint("invalid")
                await database.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
