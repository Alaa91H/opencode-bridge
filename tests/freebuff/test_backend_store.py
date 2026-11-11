from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.backend_sessions import BackendSessionStore
from bridge.infrastructure.database.sqlite import BridgeDatabase


class BackendSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = BridgeDatabase(Path(self.temporary.name) / "bridge.db")
        self.store = BackendSessionStore(self.database)
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self.temporary.cleanup()

    async def test_session_roundtrip_is_scoped_by_workspace(self) -> None:
        await self.store.upsert_session("42", "freebuff", "/work/a", "chat-1", "model-x")
        found = await self.store.get_session("42", "freebuff", "/work/a")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.session_id, "chat-1")
        self.assertEqual(found.model, "model-x")
        self.assertIsNone(await self.store.get_session("42", "freebuff", "/work/b"))

    async def test_delete_session(self) -> None:
        await self.store.upsert_session("42", "freebuff", "/work/a", "chat-1")
        self.assertTrue(await self.store.delete_session("42", "freebuff", "/work/a"))
        self.assertIsNone(await self.store.get_session("42", "freebuff", "/work/a"))

    async def test_preference_roundtrip_and_validation(self) -> None:
        self.assertIsNone(await self.store.get_preference("42"))
        await self.store.set_preference("42", "freebuff")
        self.assertEqual(await self.store.get_preference("42"), "freebuff")
        await self.store.set_preference("42", "auto")
        self.assertEqual(await self.store.get_preference("42"), "auto")
        with self.assertRaises(ValueError):
            await self.store.set_preference("42", "unknown")


if __name__ == "__main__":
    unittest.main()
