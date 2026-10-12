from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bridge.services.provider_keys import ProviderKeyManager


class ProviderKeyManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.api = AsyncMock()
        self.vault = ProviderKeyManager(Path(self.directory.name) / "private" / "keys.json", self.api)

    async def test_active_keys_rotate_on_credential_error(self):
        await self.vault.add("anthropic", "alpha", "abcdefgh-1111")
        await self.vault.add("anthropic", "beta", "abcdefgh-2222")
        await self.vault.activate("anthropic", "alpha")
        await self.vault.toggle_auto("anthropic")
        self.assertFalse(await self.vault.rotate_on_error("anthropic", quota_scope="ip"))
        self.assertTrue(await self.vault.rotate_on_error("anthropic"))
        self.assertEqual(self.vault.catalog()[1]["active"], True)
        self.assertEqual(self.api.set_provider_key.await_args.args,
                         ("anthropic", "abcdefgh-2222"))
        self.assertNotIn("abcdefgh", str(self.vault.catalog()))
        self.assertEqual(self.vault.path.stat().st_mode & 0o777, 0o600)

    async def test_never_persist_failed_activation(self):
        await self.vault.add("openai", "one", "12345678")
        self.api.set_provider_key.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await self.vault.activate("openai", "one")
        self.assertFalse(self.vault.catalog()[0]["active"])

    async def test_active_deletion_rejected(self):
        await self.vault.add("xai", "first", "12345678")
        await self.vault.activate("xai", "first")
        with self.assertRaises(ValueError):
            await self.vault.remove("xai", "first")

    async def test_duplicate_and_identifier_validation(self):
        with self.assertRaises(ValueError):
            await self.vault.add("../oops", "one", "secret123")
        await self.vault.add("openai", "one", "secret123")
        with self.assertRaises(ValueError):
            await self.vault.add("openai", "one", "secret5678")
