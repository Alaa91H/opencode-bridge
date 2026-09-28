import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from task_queue import TaskQueueStore, encode_time, utc_now


class DurableQueueTests(unittest.TestCase):
    def test_lease_heartbeat_recovery_retry_and_dead_letter(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                store = TaskQueueStore(Path(tmp) / "queue.db")
                await store.init()
                task, _ = await store.enqueue("owner", 1, "work", idempotency_key="update:1", priority=7)
                self.assertTrue(task.public_id)
                self.assertEqual(task.priority, 7)

                leased = await store.claim_next("worker-a", lease_seconds=30)
                self.assertIsNotNone(leased)
                self.assertEqual(leased.status, "leased")
                self.assertEqual(leased.attempt, 1)
                self.assertTrue(await store.heartbeat(leased.id, "worker-a", 30))
                running = await store.mark_running(leased.id, "worker-a")
                self.assertEqual(running.status, "running")

                db = await store._get_db()
                async with store._lock:
                    await db.execute(
                        "UPDATE agent_tasks SET lease_expires_at=? WHERE id=?",
                        (encode_time(utc_now() - timedelta(seconds=1)), leased.id),
                    )
                    await db.commit()
                recovered = await store.claim_next("worker-b", lease_seconds=30)
                self.assertEqual(recovered.id, leased.id)
                self.assertEqual(recovered.lease_owner, "worker-b")
                self.assertEqual(recovered.attempt, 2)

                retrying = await store.retry_or_dead_letter(
                    recovered.id, "temporary", retryable=True, max_attempts=3, retry_after=0.01
                )
                self.assertEqual(retrying.status, "retrying")
                self.assertIsNotNone(retrying.next_attempt_at)

                dead = await store.retry_or_dead_letter(
                    recovered.id, "permanent", retryable=False, max_attempts=3
                )
                self.assertEqual(dead.status, "dead_letter")
                self.assertEqual((await store.list_failed("owner"))[0].id, dead.id)
                queued = await store.retry_failed(dead.id, "owner")
                self.assertEqual(queued.status, "queued")
                await store.close()
        asyncio.run(scenario())

    def test_idempotency_key_is_unique(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                store = TaskQueueStore(Path(tmp) / "queue.db")
                await store.init()
                await store.enqueue("owner", 1, "one", idempotency_key="same")
                with self.assertRaises(Exception):
                    await store.enqueue("owner", 1, "two", idempotency_key="same")
                await store.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
