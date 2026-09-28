from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge.infrastructure.database.attachment_store import AttachmentMetadataStore
from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.infrastructure.storage.local import LocalStorage
from bridge.services.attachment_storage_service import AttachmentStorageService


class AttachmentStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = BridgeDatabase(root / "bridge.db")
        await MigrationRunner(self.db).migrate()
        self.backend = LocalStorage(root / "objects")
        self.metadata = AttachmentMetadataStore(self.db)
        self.service = AttachmentStorageService(self.backend, self.metadata)

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    def test_local_storage_is_content_addressed_and_deduplicates(self):
        first = self.backend.put_stream(io.BytesIO(b"same payload"))
        second = self.backend.put_stream(io.BytesIO(b"same payload"))
        self.assertEqual(first.key, first.sha256)
        self.assertEqual(first.key, second.key)
        self.assertEqual(self.backend.open(first.key).read(), b"same payload")

    async def test_reference_counting_and_owner_isolation(self):
        path = Path(self.temp.name) / "input.bin"
        path.write_bytes(b"shared")
        a = await self.service.ingest_path(path, owner_id="a", file_id="fa", file_unique_id="ua",
                                           claimed_mime="application/test", detected_mime="application/octet-stream",
                                           scan_state="clean")
        b = await self.service.ingest_path(path, owner_id="b", file_id="fb", file_unique_id="ub")
        self.assertEqual(a.sha256, b.sha256)
        self.assertTrue(self.backend.exists(a.storage_key))
        self.assertFalse(await self.service.release(a.id, "wrong-owner"))
        self.assertTrue(await self.service.release(a.id, "a"))
        self.assertTrue(self.backend.exists(b.storage_key))
        self.assertTrue(await self.service.release(b.id, "b"))
        self.assertFalse(self.backend.exists(b.storage_key))

    async def test_retention_cleanup_releases_expired_reference(self):
        path = Path(self.temp.name) / "expired.bin"
        path.write_bytes(b"expire me")
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        ref = await self.service.ingest_path(path, owner_id="owner", retention_until=past)
        self.assertTrue(self.backend.exists(ref.storage_key))
        self.assertEqual(await self.service.cleanup_expired(), 1)
        self.assertFalse(self.backend.exists(ref.storage_key))

    async def test_migration_has_complete_metadata_columns(self):
        db = await self.db.connect()
        async with db.execute("PRAGMA table_info(attachment_refs)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        self.assertTrue({"file_id","file_unique_id","blob_sha256","claimed_mime","detected_mime",
                         "size_bytes","owner_id","retention_until","scan_state"} <= columns)
