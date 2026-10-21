"""Tests for the consolidated SessionStore in the bridge domain layer."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from bridge.domain.sessions.session_store import SessionStore
from bridge.infrastructure.database.sqlite import BridgeDatabase


class DomainSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "sessions.db"
        self.database = BridgeDatabase(self.db_path)
        self.store = SessionStore(self.db_path, database=self.database)
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        await self.database.close()
        self.tmp.cleanup()

    async def test_create_and_get_session(self) -> None:
        session = await self.store.create_session("user-1", "oc-sess-1", model="model-a")
        self.assertEqual(session.telegram_user_id, "user-1")
        self.assertEqual(session.opencode_session_id, "oc-sess-1")
        self.assertEqual(session.model, "model-a")

        fetched = await self.store.get_session("user-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.opencode_session_id, "oc-sess-1")
        self.assertEqual(fetched.model, "model-a")

    async def test_get_missing_session_returns_none(self) -> None:
        self.assertIsNone(await self.store.get_session("unknown-user"))

    async def test_update_session(self) -> None:
        await self.store.create_session("user-1", "oc-sess-1")
        updated = await self.store.update_session("user-1", model="model-b", title="My title")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.model, "model-b")
        self.assertEqual(updated.title, "My title")
        # opencode_session_id unchanged when not passed
        self.assertEqual(updated.opencode_session_id, "oc-sess-1")

    async def test_update_nonexistent_session_returns_none(self) -> None:
        self.assertIsNone(await self.store.update_session("unknown-user", model="model-b"))

    async def test_delete_session(self) -> None:
        await self.store.create_session("user-1", "oc-sess-1")
        self.assertTrue(await self.store.delete_session("user-1"))
        self.assertFalse(await self.store.delete_session("user-1"))
        self.assertIsNone(await self.store.get_session("user-1"))

    async def test_list_sessions(self) -> None:
        await self.store.create_session("user-b", "oc-sess-b")
        await self.store.create_session("user-a", "oc-sess-a")
        sessions = await self.store.list_sessions()
        self.assertEqual(len(sessions), 2)
        ids = [s.telegram_user_id for s in sessions]
        self.assertEqual(ids, ["user-a", "user-b"])

    async def test_init_is_idempotent(self) -> None:
        # Calling init twice should not raise.
        await self.store.init()
        await self.store.create_session("user-1", "oc-sess-1")
        self.assertIsNotNone(await self.store.get_session("user-1"))


if __name__ == "__main__":
    unittest.main()
