from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.services.task_execution_service import TaskExecutionService


class FakeAttachment:
    filename = "input.txt"
    mime = "text/plain"
    kind = "document"
    size = 12
    path = Path("/tmp/input.txt")

    def to_message_part(self):
        return {"type": "file", "url": "file:///tmp/input.txt", "mime": self.mime}

    def is_direct_model_visible(self):
        return False


class FakeAttachmentStore:
    def __init__(self) -> None:
        self.cleaned = []
        self.deleted = []
        self.outputs = []

    def validate_input_records(self, records):
        return [FakeAttachment() for _ in records]

    def task_output_directory(self, task_id):
        return Path("/tmp") / f"out-{task_id}"

    def task_work_directory(self, task_id):
        return Path("/tmp") / f"work-{task_id}"

    def collect_task_outputs(self, task_id, max_files=10):
        return list(self.outputs)

    def cleanup_task_work(self, task_id):
        self.cleaned.append(task_id)

    def delete_input_records(self, records):
        self.deleted.extend(records)
        return len(records)


class FakeRepository:
    def __init__(self, task) -> None:
        self.task = task
        self.finishes = []

    async def get(self, task_id):
        return self.task

    async def finish(self, task_id, success, error=None):
        self.finishes.append((task_id, success, error))
        self.task.status = "completed" if success else "failed"


class FakeReporter:
    def __init__(self) -> None:
        self.records = []
        self.finalized = []
        self.events = []

    async def record(self, phase, message, kind="info", force=False):
        self.records.append((phase, message, kind, force))

    async def consume_events(self, client, session_id):
        self.events.append((client, session_id))

    async def finalize_text(self, text, status="completed", message="اكتمل التنفيذ."):
        self.finalized.append((text, status, message))


class FakeDelivery:
    def __init__(self) -> None:
        self.reporter = FakeReporter()
        self.sent = []
        self.ended = []
        self.formatted = []

    async def begin(self, task):
        return self.reporter

    async def send_outputs(self, task, paths):
        self.sent.append((task.id, list(paths)))
        return len(paths)

    def final_text(self, text, command_points):
        self.formatted.append((text, command_points))
        return f"points={command_points}\n{text}"

    def error_text(self, exc, operation):
        return f"{operation}: {type(exc).__name__}"

    def remaining_points(self):
        return 123

    async def end(self, task):
        self.ended.append(task.id)


class FakeAgentService:
    def __init__(self, responses):
        self.client = object()
        self.responses = list(responses)
        self.calls = []

    async def current_model(self, owner_id):
        return "session-1", "model-1"

    async def best_model_for_inputs(self, current_model, required_inputs):
        return current_model

    async def send_prompt_with_fallback(
        self,
        owner_id,
        session_id,
        prompt,
        parts,
        selected_model,
        **kwargs,
    ):
        self.calls.append(
            {
                "owner_id": owner_id,
                "session_id": session_id,
                "prompt": prompt,
                "parts": parts,
                "selected_model": selected_model,
                "kwargs": kwargs,
            }
        )
        response = self.responses.pop(0)
        return response, selected_model, 2


def make_task():
    return SimpleNamespace(
        id=7,
        owner_id="42",
        chat_id=99,
        prompt="do the work",
        execution_mode=None,
        attachments=({"path": "/tmp/input.txt"},),
        is_recurring=False,
        status="queued",
    )


class TaskExecutionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_path_is_framework_independent_and_delivers_once(self) -> None:
        task = make_task()
        repository = FakeRepository(task)
        attachments = FakeAttachmentStore()
        agent = FakeAgentService(
            [{"parts": [{"type": "text", "text": "done"}]}]
        )
        delivery = FakeDelivery()
        audits = []
        service = TaskExecutionService(
            repository,
            agent,
            attachments,
            audit_write=lambda *args, **kwargs: audits.append((args, kwargs)),
        )

        await service.execute(task, delivery)

        self.assertEqual(repository.finishes, [(7, True, None)])
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(delivery.reporter.finalized[0][1], "completed")
        self.assertIn("done", delivery.reporter.finalized[0][0])
        self.assertEqual(attachments.cleaned, [7])
        self.assertEqual(len(attachments.deleted), 1)
        self.assertEqual(delivery.ended, [7])
        self.assertTrue(any(args[0] == "task_finished" for args, _ in audits))

    async def test_empty_response_retries_once_then_fails_cleanly(self) -> None:
        task = make_task()
        repository = FakeRepository(task)
        attachments = FakeAttachmentStore()
        empty = {"parts": []}
        agent = FakeAgentService([empty, empty])
        delivery = FakeDelivery()
        service = TaskExecutionService(
            repository,
            agent,
            attachments,
            audit_write=lambda *args, **kwargs: None,
        )

        await service.execute(task, delivery)

        self.assertEqual(len(agent.calls), 2)
        self.assertEqual(repository.finishes, [(7, False, "empty_response")])
        self.assertEqual(delivery.reporter.finalized[0][1], "failed")
        phases = [item[0] for item in delivery.reporter.records]
        self.assertIn("retry", phases)
        self.assertEqual(delivery.ended, [7])

    async def test_cancelled_task_is_not_executed(self) -> None:
        task = make_task()
        task.status = "cancelled"
        repository = FakeRepository(task)
        attachments = FakeAttachmentStore()
        agent = FakeAgentService([])
        delivery = FakeDelivery()
        service = TaskExecutionService(
            repository,
            agent,
            attachments,
            audit_write=lambda *args, **kwargs: None,
        )

        await service.execute(task, delivery)

        self.assertEqual(agent.calls, [])
        self.assertEqual(repository.finishes, [])
        self.assertEqual(delivery.ended, [])


if __name__ == "__main__":
    unittest.main()
