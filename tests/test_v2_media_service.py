from __future__ import annotations

import unittest
from types import SimpleNamespace

from bridge.domain.policies import RequestGuard, RequestRejected
from bridge.services.media_service import MediaTaskService, PendingAttachmentsExpired


class FakeAttachmentStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def validate_input_records(self, records):
        for record in records:
            if not record.get("path"):
                raise ValueError("invalid record")
        return [SimpleNamespace(**record) for record in records]

    def delete_input_records(self, records):
        for record in records:
            self.deleted.append(str(record.get("path")))
        return len(records)


class FakeMediaRepository:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, int], tuple[list[dict], bool]] = {}
        self.enqueued: list[dict] = []
        self.fail_enqueue = False

    async def pop_pending_attachments(self, owner_id, chat_id):
        records, expired = self.pending.pop((owner_id, chat_id), ([], False))
        return tuple(records), expired

    async def stage_pending_attachments(
        self, owner_id, chat_id, attachments, ttl_seconds, max_count, max_total_bytes
    ):
        if len(attachments) > max_count:
            raise ValueError("too many")
        total = sum(int(item.get("size", 0)) for item in attachments)
        if total > max_total_bytes:
            raise ValueError("too large")
        key = (owner_id, chat_id)
        previous, previous_expired = self.pending.get(key, ([], False))
        expired_records = tuple(previous) if previous_expired else ()
        merged = [] if previous_expired else list(previous)
        merged.extend(attachments)
        batch = SimpleNamespace(
            attachments=tuple(merged),
            expires_at=SimpleNamespace(isoformat=lambda: "2099-01-01T00:00:00+00:00"),
        )
        self.pending[key] = (merged, False)
        return batch, expired_records

    async def purge_expired_pending_attachments(self):
        records = []
        for key, (items, expired) in list(self.pending.items()):
            if expired:
                records.extend(items)
                self.pending.pop(key)
        return tuple(records)

    async def enqueue(
        self, owner_id, chat_id, prompt, *, attachments=None,
        status_message_id=None, execution_mode=None
    ):
        if self.fail_enqueue:
            raise RuntimeError("queue failed")
        item = {
            "owner_id": owner_id,
            "chat_id": chat_id,
            "prompt": prompt,
            "attachments": list(attachments or []),
            "status_message_id": status_message_id,
            "execution_mode": execution_mode,
        }
        self.enqueued.append(item)
        return SimpleNamespace(id=len(self.enqueued)), len(self.enqueued)


def record(name: str, size: int = 1) -> dict:
    return {
        "path": f"/managed/{name}",
        "filename": name,
        "mime": "application/octet-stream",
        "size": size,
        "kind": "document",
    }


class MediaTaskServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repo = FakeMediaRepository()
        self.store = FakeAttachmentStore()
        self.guard = RequestGuard((lambda text: "blocked" if "DENY" in text else None,))
        self.service = MediaTaskService(
            self.repo,
            self.store,
            self.guard,
            pending_ttl_seconds=600,
            max_count=10,
            max_total_bytes=1000,
        )

    async def test_stage_and_merge_pending_with_fresh_records(self) -> None:
        await self.service.stage_records("u", 1, [record("old.bin")])
        queued = await self.service.queue_records(
            "u",
            1,
            "process",
            [record("new.bin")],
            status_message_id=77,
        )
        self.assertEqual(
            [item["filename"] for item in queued.attachments],
            ["old.bin", "new.bin"],
        )
        self.assertEqual(queued.task.id, 1)
        self.assertEqual(self.repo.enqueued[0]["status_message_id"], 77)

    async def test_expired_pending_is_deleted_but_fresh_upload_still_queues(self) -> None:
        self.repo.pending[("u", 1)] = ([record("expired.bin")], True)
        queued = await self.service.queue_records("u", 1, "process", [record("fresh.bin")])
        self.assertEqual([item["filename"] for item in queued.attachments], ["fresh.bin"])
        self.assertIn("/managed/expired.bin", self.store.deleted)

    async def test_policy_rejection_deletes_direct_batch(self) -> None:
        self.repo.pending[("u", 1)] = ([record("old.bin")], False)
        with self.assertRaises(RequestRejected):
            await self.service.queue_records("u", 1, "DENY", [record("fresh.bin")])
        self.assertCountEqual(
            self.store.deleted,
            ["/managed/old.bin", "/managed/fresh.bin"],
        )

    async def test_taken_pending_is_restored_on_policy_or_queue_failure(self) -> None:
        records = (record("pending.bin"),)
        with self.assertRaises(RequestRejected):
            await self.service.queue_taken_pending("u", 2, "DENY", records)
        restored, expired = self.repo.pending[("u", 2)]
        self.assertFalse(expired)
        self.assertEqual(restored[0]["filename"], "pending.bin")

        taken = await self.service.take_pending("u", 2)
        self.repo.fail_enqueue = True
        with self.assertRaises(RuntimeError):
            await self.service.queue_taken_pending("u", 2, "ok", taken)
        restored, _ = self.repo.pending[("u", 2)]
        self.assertEqual(restored[0]["filename"], "pending.bin")

    async def test_expired_taken_pending_raises_and_cleans_files(self) -> None:
        self.repo.pending[("u", 3)] = ([record("expired.bin")], True)
        with self.assertRaises(PendingAttachmentsExpired):
            await self.service.take_pending("u", 3)
        self.assertEqual(self.store.deleted, ["/managed/expired.bin"])

    async def test_discard_and_purge_are_service_operations(self) -> None:
        self.repo.pending[("u", 4)] = ([record("discard.bin")], False)
        self.assertEqual(await self.service.discard("u", 4), 1)
        self.repo.pending[("u", 5)] = ([record("purge.bin")], True)
        records, deleted = await self.service.purge_expired()
        self.assertEqual((records, deleted), (1, 1))


if __name__ == "__main__":
    unittest.main()
