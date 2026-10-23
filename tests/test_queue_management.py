"""Queue management: waiting order, removal, priority, and Telegram /queue."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.telegram.commands.tasks import TaskCommands
from bridge.telegram.rendering.tasks import render_queue
from task_queue import TaskQueueStore


class QueueStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskQueueStore(Path(self.temp.name) / "queue.db")
        await self.store.init()

    async def asyncTearDown(self) -> None:
        await self.store.database.close()
        self.temp.cleanup()

    async def test_claim_removes_task_from_waiting_and_bulk_clear_keeps_running(self) -> None:
        first, _ = await self.store.enqueue("alice", 1, "first")
        second, _ = await self.store.enqueue("alice", 1, "second")
        third, _ = await self.store.enqueue("alice", 1, "third")

        claimed = await self.store.claim_next(max_active=1)
        self.assertEqual(claimed.id, first.id)
        waiting = await self.store.list_waiting("alice")
        self.assertEqual([task.id for task in waiting], [second.id, third.id])

        removed = await self.store.delete_waiting("alice")
        self.assertEqual([task.id for task in removed], [second.id, third.id])
        self.assertEqual(await self.store.list_waiting("alice"), [])
        running = await self.store.get(first.id)
        self.assertIn(running.status, {"running", "leased"})
        self.assertIsNone(await self.store.get(second.id))

    async def test_waiting_order_matches_priority_and_owner_isolation(self) -> None:
        early, _ = await self.store.enqueue("alice", 1, "early")
        late, _ = await self.store.enqueue("alice", 1, "late")
        other, _ = await self.store.enqueue("bob", 2, "other")
        await self.store.change_priority(late.id, "alice", 1)

        waiting = await self.store.list_waiting("alice")
        self.assertEqual([task.id for task in waiting], [late.id, early.id])
        self.assertEqual([task.id for task in await self.store.list_waiting("bob")], [other.id])

    async def test_delete_waiting_reports_only_owner_rows(self) -> None:
        mine, _ = await self.store.enqueue("alice", 1, "mine")
        theirs, _ = await self.store.enqueue("bob", 2, "theirs")
        removed = await self.store.delete_waiting("alice")
        self.assertEqual([task.id for task in removed], [mine.id])
        self.assertIsNotNone(await self.store.get(theirs.id))


class QueueRendererTests(unittest.TestCase):
    def _task(self, task_id: int, prompt: str, *, status: str = "queued", priority: int = 0):
        return SimpleNamespace(
            id=task_id, public_id=f"public-{task_id}", prompt=prompt,
            status=status, priority=priority,
        )

    def test_queue_shows_running_then_ordered_waiting_with_ids(self) -> None:
        running = self._task(10, "المهمة الجارية", status="running")
        waiting = [self._task(11, "الأولى"), self._task(12, "الثانية", status="retrying")]
        text = render_queue(running, waiting, max_length=4000)
        self.assertIn("قيد التنفيذ الآن", text)
        self.assertIn("#10", text)
        self.assertIn("بانتظار التنفيذ (2)", text)
        self.assertLess(text.index("#11"), text.index("#12"))
        self.assertIn("1. #11", text)
        self.assertIn("2. #12", text)
        self.assertIn("/queue remove", text)

    def test_empty_queue_explains_automatic_serial_execution(self) -> None:
        text = render_queue(None, [], max_length=4000)
        self.assertIn("طابور التنفيذ فاضي", text)
        self.assertIn("بينتظر دوره", text)

    def test_queue_is_trimmed_to_the_message_limit(self) -> None:
        waiting = [self._task(100 + index, "x" * 200) for index in range(30)]
        text = render_queue(None, waiting, max_length=500)
        self.assertLessEqual(len(text), 500)
        self.assertIn("القائمة أطول", text)


class QueueCommandTests(unittest.IsolatedAsyncioTestCase):
    class _Service:
        def __init__(self) -> None:
            self.items = [
                SimpleNamespace(id=7, public_id="p7", prompt="الأولى", status="queued", priority=0),
                SimpleNamespace(id=8, public_id="p8", prompt="الثانية", status="queued", priority=0),
            ]
            self.running = SimpleNamespace(id=6, public_id="p6", prompt="الجارية", status="running")
            self.removed: list[int] = []

        async def queue_snapshot(self, owner_id):
            return self.running, list(self.items)

        async def remove(self, owner_id, task_id):
            task = next((item for item in self.items if item.id == task_id), None)
            if task is None:
                return None
            self.items.remove(task)
            self.removed.append(task_id)
            return task

        async def clear_waiting(self, owner_id):
            removed = list(self.items)
            self.items.clear()
            return removed

        async def change_priority(self, owner_id, task_id, delta):
            task = next((item for item in self.items if item.id == task_id), None)
            if task is None:
                return None
            task.priority += delta
            self.items.sort(key=lambda item: (-item.priority, item.id))
            return task

        async def waiting(self, owner_id):
            return list(self.items)

    def _commands(self, service):
        self.replies: list[str] = []
        self.edits: list[tuple[int, str]] = []

        async def reply(message, text):
            self.replies.append(text)

        async def create_status(bot, chat_id, text):
            return 99

        async def edit_status(bot, chat_id, message_id, text):
            self.edits.append((message_id, text))

        return TaskCommands(
            service,
            SimpleNamespace(),
            lambda: SimpleNamespace(),
            reply=reply,
            create_status=create_status,
            edit_status=edit_status,
            wake_tasks=lambda: None,
            error_message=lambda exc, operation: f"{operation}: {exc}",
            audit_write=lambda *a, **k: None,
            live_reporters={},
            progress_store=SimpleNamespace(),
            max_message_length=4000,
            research_modes={},
        )

    def _update(self):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=2),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    async def test_list_remove_up_and_clear(self) -> None:
        service = self._Service()
        commands = self._commands(service)
        update = self._update()

        await commands.queue(update, SimpleNamespace(args=[]))
        self.assertIn("#6", self.replies[-1])
        self.assertIn("1. #7", self.replies[-1])

        await commands.queue(update, SimpleNamespace(args=["remove", "7"]))
        self.assertEqual(service.removed, [7])
        self.assertIn("تم حذف المهمة #7", self.replies[-1])

        await commands.queue(update, SimpleNamespace(args=["up", "8"]))
        self.assertEqual(service.items[0].priority, 1)
        self.assertIn("المركز 1", self.replies[-1])

        await commands.queue(update, SimpleNamespace(args=["clear"]))
        self.assertEqual(service.items, [])
        self.assertIn("تم حذف 1", self.replies[-1])

    async def test_remove_refuses_unknown_id(self) -> None:
        commands = self._commands(self._Service())
        await commands.queue(self._update(), SimpleNamespace(args=["remove", "999"]))
        self.assertIn("ما في مهمة منتظرة", self.replies[-1])

    async def test_queue_acknowledges_position_only_when_waiting(self) -> None:
        service = self._Service()
        commands = self._commands(service)
        await commands._announce_queued(
            SimpleNamespace(bot=object()), "1", 2, 99, task_id=7
        )
        self.assertTrue(self.edits)
        self.assertIn("المركز 1 من 2", self.edits[-1][1])
        self.assertIn("/queue", self.edits[-1][1])

    async def test_no_acknowledgement_when_task_starts_immediately(self) -> None:
        service = self._Service()
        service.running = None
        commands = self._commands(service)
        await commands._announce_queued(
            SimpleNamespace(bot=object()), "1", 2, 99, task_id=7
        )
        self.assertEqual(self.edits, [])


if __name__ == "__main__":
    unittest.main()
