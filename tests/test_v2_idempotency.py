import asyncio
import tempfile
import unittest
from pathlib import Path

from task_queue import TaskQueueStore


class IdempotencyTests(unittest.TestCase):
    def test_duplicate_telegram_update_survives_restart(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "queue.db"
                first = TaskQueueStore(path)
                await first.init()
                task1, _, created1 = await first.enqueue_once(
                    "telegram_update", "4242", "owner", 10, "hello"
                )
                self.assertTrue(created1)
                await first.close()

                restarted = TaskQueueStore(path)
                await restarted.init()
                task2, _, created2 = await restarted.enqueue_once(
                    "telegram_update", "4242", "owner", 10, "hello"
                )
                self.assertFalse(created2)
                self.assertEqual(task1.id, task2.id)
                db = await restarted._get_db()
                async with db.execute("SELECT COUNT(*) AS n FROM agent_tasks") as cur:
                    self.assertEqual(int((await cur.fetchone())["n"]), 1)
                await restarted.close()
        asyncio.run(scenario())

    def test_duplicate_schedule_tick_is_claimed_once(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "queue.db"
                store = TaskQueueStore(path)
                await store.init()
                self.assertTrue(await store.claim_schedule_occurrence("daily-report", "2026-09-28T08:00:00Z"))
                self.assertFalse(await store.claim_schedule_occurrence("daily-report", "2026-09-28T08:00:00Z"))
                await store.close()

                restarted = TaskQueueStore(path)
                await restarted.init()
                self.assertFalse(await restarted.claim_schedule_occurrence("daily-report", "2026-09-28T08:00:00Z"))
                await restarted.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
