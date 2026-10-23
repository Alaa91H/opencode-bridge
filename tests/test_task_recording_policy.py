"""Recording policy: only scheduled and daily work keeps a terminal record.

Plain one-off prompts are removed from agent_tasks when they reach a terminal
state (completed, failed, dead_letter, cancelled). Recurring tasks, tasks
spawned by scheduled jobs, and durable daily tasks keep their records.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from task_queue import TaskQueueStore, encode_time, utc_now


class TaskRecordingPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "queue.db"
        self.store = TaskQueueStore(self.db_path)
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.temp.cleanup()

    async def _db(self):
        return await self.store._get_db()

    async def _counts(self) -> dict[str, int]:
        db = await self._db()
        async with db.execute("SELECT status, COUNT(*) FROM agent_tasks GROUP BY status") as cursor:
            rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def test_successful_plain_task_leaves_no_record(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        finished = await self.store.finish(task.id, success=True)
        self.assertIsNone(await self.store.get(task.id))
        self.assertIsNone(finished)
        self.assertEqual(await self._counts(), {})

    async def test_failed_plain_task_leaves_no_record(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        finished = await self.store.finish(task.id, success=False, error="ExampleError")
        self.assertIsNone(await self.store.get(task.id))
        self.assertIsNone(finished)
        self.assertNotIn("failed", await self._counts())

    async def test_terminated_plain_task_leaves_no_dead_letter_record(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        deferred = await self.store.retry_or_dead_letter(
            task.id, "ExampleError", retryable=False,
        )
        self.assertIsNone(deferred)
        self.assertIsNone(await self.store.get(task.id))
        self.assertNotIn("dead_letter", await self._counts())

    async def test_retrying_plain_task_stays_active(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        deferred = await self.store.retry_or_dead_letter(
            task.id, "ExampleError", retryable=True,
        )
        self.assertIsNotNone(deferred)
        self.assertEqual(deferred.status, "retrying")
        self.assertEqual((await self._counts()).get("retrying"), 1)

    async def test_cancelling_a_plain_task_removes_it(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        cancelled = await self.store.cancel(task.id, "owner")
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(await self.store.get(task.id))
        self.assertEqual(await self._counts(), {})

    async def test_recurring_task_keeps_its_record(self) -> None:
        due = utc_now() + timedelta(hours=1)
        task = await self.store.schedule(
            "owner", 10, "recurring work", due, repeat_seconds=600,
        )
        db = await self._db()
        await db.execute(
            "UPDATE agent_tasks SET due_at = ? WHERE id = ?",
            (encode_time(utc_now() - timedelta(seconds=1)), task.id),
        )
        await db.commit()
        self.assertEqual(await self.store.promote_due(), 1)
        claimed = await self.store.claim_next(worker_id="probe")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, task.id)
        finished = await self.store.finish(task.id, success=True)
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "scheduled")
        self.assertIsNotNone(finished.due_at)
        restored = await self.store.get(task.id)
        self.assertEqual(restored.status, "scheduled")

    async def test_task_spawned_by_a_scheduled_job_keeps_its_record(self) -> None:
        due = utc_now() + timedelta(hours=1)
        await self.store.create_scheduled_job(
            "owner", 10, "report", "prepare report", due, repeat_seconds=3600,
        )
        task = await self.store.enqueue_scheduled_job_now("owner", "report")
        self.assertIsNotNone(task.schedule_job_id)
        finished = await self.store.finish(task.id, success=True)
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "completed")
        restored = await self.store.get(task.id)
        self.assertEqual(restored.status, "completed")

    async def test_daily_task_keeps_its_record(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "daily digest")
        await self.store.save_checkpoint(
            task.id, {"daily_continuation": True, "session_id": "s1"},
        )
        finished = await self.store.finish(task.id, success=True)
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "completed")
        restored = await self.store.get(task.id)
        self.assertEqual(restored.status, "completed")

    async def test_recover_interruption_of_plain_task_leaves_no_record(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        await self.store.claim_next(worker_id="probe")
        await self.store.mark_running(task.id, "probe")
        touched = await self.store.recover_interrupted()
        self.assertEqual(touched, 1)
        self.assertIsNone(await self.store.get(task.id))
        self.assertEqual(await self._counts(), {})

    async def test_recover_interruption_of_recurring_task_keeps_the_row(self) -> None:
        due = utc_now() + timedelta(hours=1)
        task = await self.store.schedule(
            "owner", 10, "recurring work", due, repeat_seconds=600,
        )
        db = await self._db()
        await db.execute(
            "UPDATE agent_tasks SET due_at = ? WHERE id = ?",
            (encode_time(utc_now() - timedelta(seconds=1)), task.id),
        )
        await db.commit()
        await self.store.promote_due()
        await self.store.claim_next(worker_id="probe")
        await self.store.mark_running(task.id, "probe")
        await self.store.recover_interrupted()
        restored = await self.store.get(task.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, "failed")
        self.assertIn("انقطع", restored.last_error)

    async def test_daily_flag_false_is_treated_as_plain(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "plain work")
        await self.store.save_checkpoint(task.id, {"daily_continuation": False})
        await self.store.finish(task.id, success=True)
        self.assertIsNone(await self.store.get(task.id))

    async def test_cancelling_a_daily_task_keeps_the_row(self) -> None:
        task, _ = await self.store.enqueue("owner", 10, "daily digest")
        await self.store.save_checkpoint(task.id, {"daily_continuation": True})
        cancelled = await self.store.cancel(task.id, "owner")
        self.assertEqual(cancelled.status, "cancelled")
        restored = await self.store.get(task.id)
        self.assertEqual(restored.status, "cancelled")

    async def test_cancelling_a_scheduled_job_task_keeps_the_row(self) -> None:
        due = utc_now() + timedelta(hours=1)
        await self.store.create_scheduled_job(
            "owner", 10, "report", "prepare report", due, repeat_seconds=3600,
        )
        task = await self.store.enqueue_scheduled_job_now("owner", "report")
        cancelled = await self.store.cancel(task.id, "owner")
        self.assertEqual(cancelled.status, "cancelled")
        restored = await self.store.get(task.id)
        self.assertEqual(restored.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
