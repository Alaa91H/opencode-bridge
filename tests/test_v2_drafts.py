import asyncio
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.infrastructure.database.draft_store import DraftStore


class DraftPersistenceTests(unittest.TestCase):
    def test_versions_large_prompt_and_restart(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "bridge.db"
                db = BridgeDatabase(path)
                await MigrationRunner(db).migrate()
                store = DraftStore(db)
                draft = await store.create("owner", "large")
                self.assertEqual(draft.version, 1)
                payload = "x" * (1024 * 1024)
                draft = await store.append("owner", "large", payload, [{"name":"input.bin","size":123}])
                self.assertEqual(draft.version, 2)
                self.assertEqual(len(draft.prompt_text), len(payload))
                self.assertEqual(draft.attachments[0]["name"], "input.bin")
                await db.close()

                db2 = BridgeDatabase(path)
                await MigrationRunner(db2).apply()
                restored = await DraftStore(db2).get("owner", "large")
                self.assertIsNotNone(restored)
                self.assertEqual(restored.version, 2)
                self.assertEqual(restored.prompt_text, payload)
                cleared = await DraftStore(db2).clear("owner", "large")
                self.assertEqual(cleared.version, 3)
                self.assertEqual(cleared.prompt_text, "")
                conn = await db2.connect()
                async with conn.execute("SELECT COUNT(*) n FROM draft_versions WHERE draft_id=?", (restored.id,)) as cur:
                    self.assertEqual(int((await cur.fetchone())["n"]), 3)
                await db2.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
