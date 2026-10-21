from __future__ import annotations

import unittest
from types import SimpleNamespace

from bridge.domain.policies import RequestGuard, RequestRejected
from bridge.services.task_service import TaskApplicationService
from prompt_enhancer import ResearchMode


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks = []
        self.running = None
        self.latest = None

    async def enqueue(
        self, owner_id, chat_id, prompt, *, attachments=None,
        execution_mode=None, status_message_id=None
    ):
        task = SimpleNamespace(
            id=len(self.tasks) + 1,
            owner_id=owner_id,
            chat_id=chat_id,
            prompt=prompt,
            status="queued",
            attachments=tuple(attachments or ()),
            status_message_id=status_message_id,
            execution_mode=execution_mode,
        )
        self.tasks.append(task)
        self.latest = task
        return task, len(self.tasks)

    async def list_active(self, owner_id):
        return [task for task in self.tasks if task.owner_id == owner_id]

    async def latest_active_for_owner(self, owner_id):
        return self.latest if self.latest and self.latest.owner_id == owner_id else None

    async def cancel(self, task_id, owner_id):
        if self.latest and self.latest.id == task_id and self.latest.owner_id == owner_id:
            self.latest.status = "cancelled"
            return self.latest
        return None

    async def cancel_running_for_owner(self, owner_id):
        if self.running and self.running.owner_id == owner_id:
            self.running.status = "cancelled"
            return self.running
        return None


class FakeAgent:
    def __init__(self) -> None:
        self.aborts = []

    async def abort_current(self, owner_id):
        self.aborts.append(owner_id)
        return True


class FakeAttachments:
    def __init__(self) -> None:
        self.deleted = []

    def delete_input_records(self, records):
        self.deleted.extend(records)
        return len(records)


class TaskApplicationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repo = FakeTaskRepository()
        self.agent = FakeAgent()
        self.attachments = FakeAttachments()
        self.guard = RequestGuard((lambda value: "blocked" if value == "deny" else None,))
        self.service = TaskApplicationService(
            self.repo,
            self.agent,
            self.attachments,
            self.guard,
        )

    async def test_enqueue_prompt_owns_policy_and_execution_mode(self) -> None:
        with self.assertRaises(RequestRejected):
            await self.service.enqueue_prompt("u", 1, "deny")

        queued = await self.service.enqueue_prompt(
            "u",
            1,
            "research this",
            status_message_id=42,
            execution_mode=ResearchMode.SEARCH,
        )
        self.assertEqual(queued.task.status_message_id, 42)
        self.assertEqual(queued.task.execution_mode, ResearchMode.SEARCH.value)
        self.assertEqual(queued.queue_position, 1)

    async def test_latest_and_active_are_repository_use_cases(self) -> None:
        await self.service.enqueue_prompt("u", 1, "hello")
        self.assertEqual(len(await self.service.active("u")), 1)
        self.assertEqual((await self.service.latest_active("u")).prompt, "hello")

    async def test_abort_running_stops_agent_even_without_running_row(self) -> None:
        result = await self.service.abort_running("u")
        self.assertIsNone(result.cancelled_task)
        self.assertTrue(result.agent_stopped)
        self.assertEqual(self.agent.aborts, ["u"])

    async def test_cancel_current_cleans_queued_attachments(self) -> None:
        task, _ = await self.repo.enqueue(
            "u",
            1,
            "hello",
            attachments=[{"path": "/managed/a"}],
        )
        cancelled = await self.service.cancel_current("u")
        self.assertEqual(cancelled.id, task.id)
        self.assertEqual(self.attachments.deleted, [{"path": "/managed/a"}])


if __name__ == "__main__":
    unittest.main()
