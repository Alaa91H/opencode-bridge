"""SQLite integration: bounded priority and owner-scoped deletion."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from task_queue import TaskQueueStore


class TaskPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskQueueStore(Path(self.temp.name) / "queue.db")
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.database.close()
        self.temp.cleanup()

    async def test_higher_priority_runs_first_and_delete_is_owner_scoped(self):
        first, _ = await self.store.enqueue("alice", 1, "first task")
        second, _ = await self.store.enqueue("alice", 1, "second task")
        self.assertIsNone(await self.store.change_priority(second.id, "bob", 1))
        moved = await self.store.change_priority(second.id, "alice", 1)
        self.assertEqual(moved.priority, 1)
        self.assertEqual((await self.store.claim_next(max_active=1)).id, second.id)
        self.assertIsNone(await self.store.delete_user_task(second.id, "alice"))
        self.assertIsNone(await self.store.delete_user_task(first.id, "bob"))
        self.assertIsNotNone(await self.store.delete_user_task(first.id, "alice"))
        self.assertIsNone(await self.store.get(first.id))

    async def test_waiting_tasks_move_in_claim_order_and_respect_owner(self):
        first, _ = await self.store.enqueue("alice", 1, "first")
        second, _ = await self.store.enqueue("alice", 1, "second")
        third, _ = await self.store.enqueue("alice", 1, "third")

        moved = await self.store.move_waiting(third.id, "alice", -1)
        self.assertEqual(moved.id, third.id)
        waiting = await self.store.list_waiting("alice")
        self.assertEqual([task.id for task in waiting], [first.id, third.id, second.id])
        self.assertEqual([task.priority for task in waiting], [2, 1, 0])

        self.assertIsNone(await self.store.move_waiting(third.id, "bob", -1))
        edge = await self.store.move_waiting(first.id, "alice", -1)
        self.assertEqual(edge.id, first.id)
        self.assertEqual([task.id for task in await self.store.list_waiting("alice")],
                         [first.id, third.id, second.id])
        with self.assertRaises(ValueError):
            await self.store.move_waiting(third.id, "alice", 2)
        self.assertEqual((await self.store.claim_next(max_active=1)).id, first.id)
        await self.store.finish(first.id, True)
        self.assertEqual((await self.store.claim_next(max_active=1)).id, third.id)

    async def test_prompt_edit_only_changes_waiting_owner_tasks(self):
        task, _ = await self.store.enqueue("alice", 1, "original")
        updated = await self.store.update_waiting_prompt(
            task.id, "alice", "  replacement instruction  ")
        self.assertEqual(updated.prompt, "replacement instruction")
        self.assertIsNone(await self.store.update_waiting_prompt(
            task.id, "bob", "hijack"))
        with self.assertRaises(ValueError):
            await self.store.update_waiting_prompt(task.id, "alice", "   ")
        await self.store.claim_next(max_active=1)
        self.assertIsNone(await self.store.update_waiting_prompt(
            task.id, "alice", "too late"))

    async def test_priority_bounds_and_invalid_delta(self):
        task, _ = await self.store.enqueue("alice", 1, "task")
        with self.assertRaises(ValueError):
            await self.store.change_priority(task.id, "alice", 42)
        for _ in range(105):
            await self.store.change_priority(task.id, "alice", 1)
        self.assertEqual((await self.store.get(task.id)).priority, 100)
