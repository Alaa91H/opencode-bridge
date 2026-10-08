from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from bridge.services.task_execution_service import TaskExecutionService
from bridge.services.task_service import TaskApplicationService
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

    async def test_zen_free_quota_pause_survives_store_reopen(self):
        reset_at = utc_now() + timedelta(hours=8)
        await self.store.set_provider_quota_pause("opencode_zen_free", reset_at)
        self.assertEqual(
            await self.store.get_provider_quota_pause("opencode_zen_free"),
            reset_at,
        )

        await self.store.close()
        self.store = TaskQueueStore(Path(self.tmp.name) / "queue.db")
        await self.store.init()
        self.assertEqual(
            await self.store.get_provider_quota_pause("opencode_zen_free"),
            reset_at,
        )

    async def test_sqlite_claims_never_exceed_three_active_tasks_globally(self):
        for owner in ("a", "b", "c", "d"):
            await self.store.enqueue(owner, 1, f"work for {owner}")

        claimed = [await self.store.claim_next(max_active=99) for _ in range(3)]

        self.assertTrue(all(task is not None for task in claimed))
        self.assertIsNone(await self.store.claim_next(max_active=99))

    async def test_task_is_held_without_model_request_during_zen_free_pause(self):
        first, _ = await self.store.enqueue("a", 1, "continue work")
        task = await self.store.claim_next()
        await self.store.mark_running(task.id, task.lease_owner)
        reset_at = utc_now() + timedelta(hours=8)
        await self.store.set_provider_quota_pause("opencode_zen_free", reset_at)

        class Agent:
            client = object()

            async def current_model(self, owner_id):
                return "saved-session", "opencode/muse-spark-1.3-contributor-free"

            async def send_prompt_with_fallback(self, *args, **kwargs):
                raise AssertionError("a quota-paused model must not receive a request")

        service = TaskExecutionService(
            self.store,
            Agent(),
            FakeAttachmentStore(),
            audit_write=lambda *a, **k: None,
        )
        await service.execute(task, FakeDelivery())

        deferred = await self.store.get(first.id)
        self.assertEqual(deferred.status, "retrying")
        self.assertEqual(deferred.checkpoint["retry_category"], "zen_free_quota")
        self.assertGreater(deferred.next_attempt_at, reset_at - timedelta(seconds=2))

    async def test_free_usage_limit_opens_global_pause_until_utc_midnight(self):
        first, _ = await self.store.enqueue("a", 1, "continue work")
        task = await self.store.claim_next()
        await self.store.mark_running(task.id, task.lease_owner)

        class Agent:
            client = object()

            async def current_model(self, owner_id):
                return "saved-session", "opencode/muse-spark-1.3-contributor-free"

            async def send_prompt_with_fallback(self, *args, **kwargs):
                response = httpx.Response(
                    429,
                    json={"name": "FreeUsageLimitError"},
                    request=httpx.Request("POST", "http://agent/message"),
                )
                raise httpx.HTTPStatusError("quota", request=response.request, response=response)

        service = TaskExecutionService(
            self.store,
            Agent(),
            FakeAttachmentStore(),
            audit_write=lambda *a, **k: None,
        )
        await service.execute(task, FakeDelivery())

        deferred = await self.store.get(first.id)
        reset_at = await self.store.get_provider_quota_pause("opencode_zen_free")
        self.assertEqual(deferred.status, "retrying")
        self.assertEqual(deferred.checkpoint["retry_category"], "zen_free_quota")
        self.assertEqual(reset_at.hour, 0)
        self.assertEqual(reset_at.minute, 0)

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

    async def test_quota_retry_remains_durable_after_thousands_of_attempts(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        await self.store.claim_next()
        db = await self.store._get_db()
        await db.execute("UPDATE agent_tasks SET attempt=2000 WHERE id=?", (first.id,))
        await db.commit()
        task = await self.store.retry_or_dead_letter(first.id, "quota", retryable=True, max_attempts=None, retry_after=60)
        self.assertEqual(task.status, "retrying")
        self.assertLessEqual((task.next_attempt_at - utc_now()).total_seconds(), 60)
        await self.store.close()
        self.store = TaskQueueStore(Path(self.tmp.name) / "queue.db")
        await self.store.init()
        await self.store.recover_interrupted()
        self.assertEqual((await self.store.get(first.id)).status, "retrying")

    async def test_cancellation_cannot_be_undone_by_deferred_failure(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        await self.store.claim_next()
        await self.store.cancel(first.id, "a")
        current = await self.store.retry_or_dead_letter(first.id, "quota", retryable=True, max_attempts=None)
        self.assertEqual(current.status, "cancelled")

    async def test_quota_then_restart_then_success_resumes_same_task_and_session(self):
        first, _ = await self.store.enqueue("a", 1, "finish issues in order", attachments=[{"path": "/tmp/input.txt"}])
        attachments = FakeAttachmentStore()
        calls = []

        class Agent:
            client = object()
            model_calls = 0

            async def current_model(self, owner_id):
                self.model_calls += 1
                return "original-session", "provider/model"

            async def send_prompt_with_fallback(self, owner, session, prompt, parts, model, **kwargs):
                calls.append((session, prompt, kwargs["message_id"]))
                if len(calls) == 1:
                    return {"info": {"error": {"data": {"statusCode": 429}}}}, model
                return {"parts": [{"type": "text", "text": "completed"}]}, model

        agent = Agent()

        async def execute(task):
            service = TaskExecutionService(self.store, agent, attachments, audit_write=lambda *a, **k: None)
            await service.execute(task, FakeDelivery())

        worker = TaskServiceV3(self.store, execute)
        await worker._execute(await self.store.claim_next())
        self.assertEqual((await self.store.get(first.id)).status, "retrying")
        await self.store.close()
        self.store = TaskQueueStore(Path(self.tmp.name) / "queue.db")
        await self.store.init()
        await self.store.recover_interrupted()
        db = await self.store._get_db()
        await db.execute("UPDATE agent_tasks SET next_attempt_at=NULL WHERE id=?", (first.id,))
        await db.commit()
        worker = TaskServiceV3(self.store, execute)
        await worker._execute(await self.store.claim_next())
        self.assertEqual((await self.store.get(first.id)).status, "completed")
        self.assertEqual(agent.model_calls, 1)
        self.assertEqual([call[0] for call in calls], ["original-session", "original-session"])
        self.assertNotEqual(calls[0][2], calls[1][2])
        self.assertIn("Resume the existing task", calls[1][1])
        self.assertEqual(attachments.cleaned, [first.id])
        self.assertEqual(len(attachments.deleted), 1)

    async def test_cancel_deferred_work_aborts_persisted_session_and_releases_queue(self):
        first, _ = await self.store.enqueue("a", 1, "first")
        await self.store.claim_next()
        await self.store.retry_or_dead_letter(first.id, "quota", retryable=True, max_attempts=None,
                                             checkpoint={"session_id": "saved"})
        later, _ = await self.store.enqueue("a", 1, "second")
        aborted = []

        class Client:
            async def abort_session(self, session_id):
                aborted.append(session_id)

        class Agent:
            client = Client()

        service = TaskApplicationService(self.store, Agent(), FakeAttachmentStore(), None)
        self.assertIsNone(await service.cancel(first.id, "other-owner"))
        self.assertEqual((await service.cancel(first.id, "a")).status, "cancelled")
        self.assertEqual(aborted, ["saved"])
        self.assertEqual((await self.store.claim_next()).id, later.id)

    async def test_notification_and_delivery_errors_cannot_terminalize_deferred_work(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        attachments = FakeAttachmentStore()
        delivery = FakeDelivery()

        async def broken_delivery(*args, **kwargs):
            raise ConnectionError("Telegram unavailable")

        delivery.reporter.finalize_text = broken_delivery
        delivery.end = broken_delivery

        class Agent:
            client = object()

            async def current_model(self, owner):
                return "saved", "provider/model"

            async def send_prompt_with_fallback(self, *args, **kwargs):
                return {"info": {"error": {"data": {"statusCode": 429}}}}, "provider/model"

        async def execute(task):
            service = TaskExecutionService(self.store, Agent(), attachments, audit_write=lambda *a, **k: None)
            await service.execute(task, delivery)

        worker = TaskServiceV3(self.store, execute)
        await worker._execute(await self.store.claim_next())
        self.assertEqual((await self.store.get(first.id)).status, "retrying")
        self.assertEqual(attachments.cleaned, [])

    async def test_finish_cannot_overwrite_concurrent_cancellation(self):
        first, _ = await self.store.enqueue("a", 1, "work")
        original_get = self.store.get
        cancelled = False

        async def get_then_cancel(task_id):
            nonlocal cancelled
            snapshot = await original_get(task_id)
            if not cancelled:
                cancelled = True
                await self.store.cancel(task_id, "a")
            return snapshot

        with patch.object(self.store, "get", side_effect=get_then_cancel):
            await self.store.finish(first.id, success=True)
        self.assertEqual((await self.store.get(first.id)).status, "cancelled")
