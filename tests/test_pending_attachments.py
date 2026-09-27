from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from task_queue import TaskQueueStore, encode_time, utc_now


def record(path: str, size: int = 4) -> dict[str, object]:
    return {
        "path": path,
        "filename": Path(path).name,
        "mime": "application/octet-stream",
        "size": size,
        "kind": "document",
    }


class PendingAttachmentStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "queue.db"
        self.store = TaskQueueStore(self.db_path)
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.temp.cleanup()

    async def test_pending_batch_survives_store_restart_until_instruction_arrives(self) -> None:
        batch, expired = await self.store.stage_pending_attachments(
            "owner",
            10,
            [record("/managed/one.bin")],
            ttl_seconds=600,
            max_count=10,
            max_total_bytes=1024,
        )
        self.assertFalse(expired)
        self.assertEqual(len(batch.attachments), 1)
        await self.store.close()

        self.store = TaskQueueStore(self.db_path)
        await self.store.init()
        attachments, is_expired = await self.store.pop_pending_attachments("owner", 10)
        self.assertFalse(is_expired)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["filename"], "one.bin")

        empty, _ = await self.store.pop_pending_attachments("owner", 10)
        self.assertEqual(empty, ())

    async def test_multiple_uploads_merge_and_duplicate_paths_are_ignored(self) -> None:
        await self.store.stage_pending_attachments(
            "owner",
            11,
            [record("/managed/one.bin")],
            ttl_seconds=600,
            max_count=3,
            max_total_bytes=1024,
        )
        batch, _ = await self.store.stage_pending_attachments(
            "owner",
            11,
            [record("/managed/one.bin"), record("/managed/two.bin")],
            ttl_seconds=600,
            max_count=3,
            max_total_bytes=1024,
        )
        self.assertEqual([item["filename"] for item in batch.attachments], ["one.bin", "two.bin"])

    async def test_staging_rejects_count_and_total_size_overflow(self) -> None:
        with self.assertRaises(ValueError):
            await self.store.stage_pending_attachments(
                "owner",
                12,
                [record("/managed/one.bin"), record("/managed/two.bin")],
                ttl_seconds=600,
                max_count=1,
                max_total_bytes=1024,
            )

        with self.assertRaises(ValueError):
            await self.store.stage_pending_attachments(
                "owner",
                13,
                [record("/managed/large.bin", size=2048)],
                ttl_seconds=600,
                max_count=2,
                max_total_bytes=1024,
            )

    async def test_expired_batches_are_purged_and_records_returned_for_file_cleanup(self) -> None:
        await self.store.stage_pending_attachments(
            "owner",
            14,
            [record("/managed/expired.bin")],
            ttl_seconds=600,
            max_count=10,
            max_total_bytes=1024,
        )
        db = await self.store._get_db()
        await db.execute(
            "UPDATE pending_attachment_batches SET expires_at = ? WHERE owner_id = ? AND chat_id = ?",
            (encode_time(utc_now() - timedelta(minutes=1)), "owner", 14),
        )
        await db.commit()

        expired = await self.store.purge_expired_pending_attachments()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["filename"], "expired.bin")
        attachments, is_expired = await self.store.pop_pending_attachments("owner", 14)
        self.assertEqual(attachments, ())
        self.assertFalse(is_expired)


if __name__ == "__main__":
    unittest.main()
