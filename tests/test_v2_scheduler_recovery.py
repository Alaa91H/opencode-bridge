import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from task_queue import TaskQueueStore, utc_now


class SchedulerRecoveryTests(unittest.TestCase):
    def test_due_schedule_recovers_once_after_restart(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "queue.db"
                store = TaskQueueStore(path)
                await store.init()
                job = await store.create_scheduled_job("u", 1, "once", "work", utc_now()+timedelta(seconds=1))
                db = await store._get_db()
                past = (utc_now()-timedelta(minutes=5)).isoformat()
                await db.execute("UPDATE scheduled_jobs SET next_run_at=? WHERE id=?", (past, job.id))
                await db.commit()
                await store.close()

                restarted = TaskQueueStore(path)
                await restarted.init()
                self.assertEqual(await restarted.promote_due(), 1)
                self.assertEqual(await restarted.promote_due(), 0)
                db = await restarted._get_db()
                async with db.execute("SELECT COUNT(*) n FROM agent_tasks WHERE schedule_job_id=?", (job.id,)) as cur:
                    self.assertEqual(int((await cur.fetchone())["n"]), 1)
                async with db.execute("SELECT COUNT(*) n FROM schedule_runs WHERE schedule_id=?", (job.id,)) as cur:
                    self.assertEqual(int((await cur.fetchone())["n"]), 1)
                await restarted.close()
        asyncio.run(scenario())

    def test_overlap_forbid_records_skipped_occurrence(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                store = TaskQueueStore(Path(tmp)/"queue.db")
                await store.init()
                job = await store.create_scheduled_job(
                    "u", 1, "repeat", "work", utc_now()+timedelta(seconds=1), repeat_seconds=300
                )
                db = await store._get_db()
                past = (utc_now()-timedelta(minutes=10)).isoformat()
                await db.execute("UPDATE scheduled_jobs SET next_run_at=? WHERE id=?", (past, job.id))
                await db.execute(
                    """INSERT INTO agent_tasks(owner_id,chat_id,prompt,status,created_at,updated_at,sequence,
                       attachments_json,schedule_job_id,public_id) VALUES ('u',1,'old','running',?,?,1,'[]',?,?)""",
                    (past,past,job.id,"active-public-id"),
                )
                await db.commit()
                self.assertEqual(await store.promote_due(), 0)
                history = await store.list_schedule_history("u","repeat")
                self.assertEqual(history[0]["status"], "skipped")
                self.assertEqual(history[0]["error"], "overlap forbidden")
                await store.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
