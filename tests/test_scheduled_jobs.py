from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from task_queue import TaskQueueStore, encode_time, utc_now


class ScheduledJobStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "queue.db"
        self.store = TaskQueueStore(self.db_path)
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.temp.cleanup()

    async def test_named_schedule_persists_across_restart(self) -> None:
        due = utc_now() + timedelta(hours=2)
        created = await self.store.create_scheduled_job(
            "owner",
            10,
            "daily-report",
            "prepare report",
            due,
            repeat_seconds=86400,
        )
        self.assertTrue(created.enabled)
        self.assertEqual(created.name, "daily-report")
        await self.store.close()

        self.store = TaskQueueStore(self.db_path)
        await self.store.init()
        restored = await self.store.get_scheduled_job("owner", "DAILY-REPORT")
        self.assertIsNotNone(restored)
        self.assertEqual(restored.prompt, "prepare report")
        self.assertEqual(restored.repeat_seconds, 86400)

    async def test_edit_append_rename_pause_resume_and_delete(self) -> None:
        due = utc_now() + timedelta(hours=1)
        await self.store.create_scheduled_job("owner", 11, "job", "first", due)

        edited = await self.store.set_scheduled_job_prompt("owner", "job", "replacement")
        self.assertEqual(edited.prompt, "replacement")

        appended = await self.store.append_scheduled_job_prompt("owner", "job", "second line")
        self.assertEqual(appended.prompt, "replacement\nsecond line")

        renamed = await self.store.rename_scheduled_job("owner", "job", "renamed")
        self.assertEqual(renamed.name, "renamed")

        paused = await self.store.set_scheduled_job_enabled("owner", "renamed", False)
        self.assertFalse(paused.enabled)

        resumed = await self.store.set_scheduled_job_enabled("owner", "renamed", True)
        self.assertTrue(resumed.enabled)
        self.assertIsNotNone(resumed.next_run_at)

        self.assertTrue(await self.store.delete_scheduled_job("owner", "renamed"))
        self.assertIsNone(await self.store.get_scheduled_job("owner", "renamed"))

    async def test_due_recurring_schedule_materializes_one_execution_and_advances(self) -> None:
        job = await self.store.create_scheduled_job(
            "owner",
            12,
            "heartbeat",
            "check services",
            utc_now() + timedelta(hours=1),
            repeat_seconds=600,
        )
        db = await self.store._get_db()
        await db.execute(
            "UPDATE scheduled_jobs SET next_run_at = ? WHERE id = ?",
            (encode_time(utc_now() - timedelta(seconds=1)), job.id),
        )
        await db.commit()

        promoted = await self.store.promote_due()
        self.assertEqual(promoted, 1)
        active = await self.store.list_active("owner")
        executions = [item for item in active if item.schedule_job_id == job.id]
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].prompt, "check services")

        refreshed = await self.store.get_scheduled_job("owner", "heartbeat")
        self.assertTrue(refreshed.enabled)
        self.assertGreater(refreshed.next_run_at, utc_now())

        promoted_again = await self.store.promote_due()
        self.assertEqual(promoted_again, 0)

    async def test_one_time_schedule_disables_after_materialization(self) -> None:
        job = await self.store.create_scheduled_job(
            "owner",
            13,
            "once",
            "run once",
            utc_now() + timedelta(hours=1),
        )
        db = await self.store._get_db()
        await db.execute(
            "UPDATE scheduled_jobs SET next_run_at = ? WHERE id = ?",
            (encode_time(utc_now() - timedelta(seconds=1)), job.id),
        )
        await db.commit()

        self.assertEqual(await self.store.promote_due(), 1)
        refreshed = await self.store.get_scheduled_job("owner", "once")
        self.assertFalse(refreshed.enabled)
        self.assertIsNone(refreshed.next_run_at)

    async def test_manual_run_keeps_persistent_schedule_definition(self) -> None:
        job = await self.store.create_scheduled_job(
            "owner",
            14,
            "manual",
            "do work",
            utc_now() + timedelta(hours=3),
            repeat_seconds=3600,
        )
        task = await self.store.enqueue_scheduled_job_now("owner", "manual", status_message_id=777)
        self.assertIsNotNone(task)
        self.assertEqual(task.schedule_job_id, job.id)
        self.assertEqual(task.status_message_id, 777)
        still_there = await self.store.get_scheduled_job("owner", "manual")
        self.assertTrue(still_there.enabled)
        self.assertEqual(still_there.repeat_seconds, 3600)

    async def test_duplicate_names_are_case_insensitive_per_owner(self) -> None:
        due = utc_now() + timedelta(hours=1)
        await self.store.create_scheduled_job("owner", 15, "Backup", "one", due)
        with self.assertRaises(ValueError):
            await self.store.create_scheduled_job("owner", 15, "backup", "two", due)

        other = await self.store.create_scheduled_job("other", 15, "backup", "two", due)
        self.assertEqual(other.owner_id, "other")

    async def test_large_prompt_can_be_built_in_chunks_and_survive_restart(self) -> None:
        due = utc_now() + timedelta(hours=1)
        first = "A" * 3000
        await self.store.create_scheduled_job("owner", 16, "huge", first, due)
        chunks = [str(index) + ":" + ("B" * 3000) for index in range(20)]
        for chunk in chunks:
            await self.store.append_scheduled_job_prompt("owner", "huge", chunk)

        job = await self.store.get_scheduled_job("owner", "huge")
        self.assertGreater(len(job.prompt), 60000)
        self.assertTrue(job.prompt.startswith(first))
        self.assertTrue(job.prompt.endswith(chunks[-1]))

        expected = job.prompt
        await self.store.close()
        self.store = TaskQueueStore(self.db_path)
        await self.store.init()
        restored = await self.store.get_scheduled_job("owner", "huge")
        self.assertEqual(restored.prompt, expected)

    async def test_timing_can_switch_from_recurring_to_one_time(self) -> None:
        due = utc_now() + timedelta(hours=1)
        await self.store.create_scheduled_job(
            "owner",
            17,
            "switchable",
            "do it",
            due,
            repeat_seconds=600,
        )
        new_due = utc_now() + timedelta(hours=3)
        changed = await self.store.update_scheduled_job_timing(
            "owner",
            "switchable",
            new_due,
            repeat_seconds=None,
        )
        self.assertIsNone(changed.repeat_seconds)
        self.assertAlmostEqual(
            changed.next_run_at.timestamp(),
            new_due.timestamp(),
            delta=1.0,
        )

    async def test_schedule_records_execution_failure_without_losing_definition(self) -> None:
        due = utc_now() + timedelta(hours=1)
        job = await self.store.create_scheduled_job(
            "owner",
            18,
            "durable",
            "do it",
            due,
            repeat_seconds=600,
        )
        task = await self.store.enqueue_scheduled_job_now("owner", "durable")
        await self.store.claim_next()
        await self.store.finish(task.id, success=False, error="ExampleError")

        refreshed = await self.store.get_scheduled_job("owner", "durable")
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.last_error, "ExampleError")
        self.assertTrue(refreshed.enabled)
        self.assertEqual(refreshed.id, job.id)


if __name__ == "__main__":
    unittest.main()
