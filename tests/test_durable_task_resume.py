from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import httpx

from bridge.services.task_execution_service import TaskExecutionService
from task_queue import TaskQueueStore, encode_time, utc_now
from task_service_v3 import TaskServiceV3
from tests.test_v2_task_execution_service import FakeAttachmentStore, FakeDelivery


class DurableTaskResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskQueueStore(Path(self.tmp.name) / "queue.db")
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    async def test_deferred_task_blocks_later_owner_tasks_but_not_other_owners(self):
        first, _ = await self.store.enqueue("a", 1, "first")
        await self.store.claim_next()
        await self.store.retry_or_dead_letter(first.id, "quota", retryable=True, retry_after=86400)
        later, _ = await self.store.enqueue("a", 1, "second", priority=99)
        other, _ = await self.store.enqueue("b", 2, "independent")
        claimed = await self.store.claim_next()
        self.assertEqual(claimed.id, other.id)
        self.assertNotEqual(claimed.id, later.id)

    async def test_deferred_task_remains_visible_and_can_be_cancelled(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        await self.store.claim_next()
        await self.store.retry_or_dead_letter(first.id, "quota", retryable=True, retry_after=86400)
        self.assertEqual([task.id for task in await self.store.list_active("a")], [first.id])
        self.assertEqual((await self.store.latest_active_for_owner("a")).id, first.id)
        self.assertEqual((await self.store.cancel(first.id, "a")).status, "cancelled")

    async def test_worker_marks_running_before_executor_and_completes(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        claimed = await self.store.claim_next()
        seen = []

        async def execute(task):
            seen.append((await self.store.get(task.id)).status)

        service = TaskServiceV3(self.store, execute, poll_seconds=500)
        await service._execute(claimed)
        self.assertEqual(seen, ["running"])
        self.assertEqual((await self.store.get(first.id)).status, "completed")
        self.assertLessEqual(service.poll_seconds, 60)

    async def test_provider_quota_does_not_fail_or_delete_task_inputs(self):
        first, _ = await self.store.enqueue("a", 1, "finish all issues", attachments=[{"path": "/tmp/input.txt"}])
        task = await self.store.claim_next()
        await self.store.mark_running(task.id, task.lease_owner)
        attachments = FakeAttachmentStore()
        delivery = FakeDelivery()

        class Agent:
            client = object()

            async def current_model(self, owner_id):
                return "session-saved", "provider/model"

            async def send_prompt_with_fallback(self, *args, **kwargs):
                response = httpx.Response(429, json={"error": {"code": "insufficient_quota"}},
                                          headers={"Retry-After": "86400"},
                                          request=httpx.Request("POST", "http://agent/message"))
                raise httpx.HTTPStatusError("quota", request=response.request, response=response)

        service = TaskExecutionService(self.store, Agent(), attachments, audit_write=lambda *a, **k: None)
        await service.execute(task, delivery)
        current = await self.store.get(first.id)
        self.assertEqual(current.status, "retrying")
        self.assertEqual(current.checkpoint["session_id"], "session-saved")
        self.assertEqual(current.checkpoint["retry_category"], "quota")
        self.assertEqual(attachments.cleaned, [])
        self.assertEqual(attachments.deleted, [])
        self.assertGreater(current.next_attempt_at, utc_now() + timedelta(hours=23))

    async def test_restart_recovers_checkpointed_running_task_instead_of_failing(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        task = await self.store.claim_next()
        await self.store.mark_running(task.id, task.lease_owner)
        db = await self.store._get_db()
        await db.execute("UPDATE agent_tasks SET checkpoint_json=? WHERE id=?",
                         ('{"session_id":"saved","message_id":"msg_saved"}', first.id))
        await db.commit()
        self.assertEqual(await self.store.recover_interrupted(), 1)
        current = await self.store.get(first.id)
        self.assertEqual(current.status, "queued")
        self.assertEqual(current.checkpoint["session_id"], "saved")
        self.assertIsNone(current.completed_at)

    async def test_resumption_keeps_task_id_and_queue_position(self):
        first, _ = await self.store.enqueue("a", 1, "first")
        await self.store.claim_next()
        await self.store.retry_or_dead_letter(first.id, "quota", retryable=True)
        await self.store.enqueue("a", 1, "second", priority=99)
        db = await self.store._get_db()
        await db.execute("UPDATE agent_tasks SET next_attempt_at=? WHERE id=?",
                         (encode_time(utc_now() - timedelta(seconds=1)), first.id))
        await db.commit()
        self.assertEqual((await self.store.claim_next()).id, first.id)
