"""Overflow truth-in-delivery: over-long answers materialize as answer-full.md."""

# Cross-test fixture imports use a local testing-only namespace.
# ruff: noqa: I001

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.services.task_execution_service import TaskExecutionService
from test_v2_task_execution_service import (
    FakeAgentService,
    FakeAttachmentStore,
    FakeDelivery,
    FakeRepository,
)


class TmpAttachmentStore(FakeAttachmentStore):
    def __init__(self, root: str) -> None:
        super().__init__()
        self.root = Path(root)

    def task_output_directory(self, task_id):
        directory = self.root / f"out-{task_id}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def task_work_directory(self, task_id):
        directory = self.root / f"work-{task_id}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


class OverflowFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlong_answer_is_saved_and_sent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Longer than the follow-up budget can carry (25 pages), so the
            # remainder must materialize as a file instead of being dropped.
            long_text = "كلمة " * 24000  # ~120k chars > 25 pages
            task = SimpleNamespace(
                id=7, owner_id="42", chat_id=99, prompt="do the work",
                execution_mode=None, attachments=(), is_recurring=False,
                status="queued",
            )
            repository = FakeRepository(task)
            attachments = TmpAttachmentStore(tmp)
            agent = FakeAgentService([{"parts": [{"type": "text", "text": long_text}]}])
            delivery = FakeDelivery()
            service = TaskExecutionService(
                repository, agent, attachments, audit_write=lambda *a, **k: None)
            await service.execute(task, delivery)
            saved = Path(tmp) / "out-7" / "answer-full.md"
            self.assertTrue(saved.is_file())
            self.assertIn(long_text[:100], saved.read_text(encoding="utf-8"))
            sent_paths = [str(path) for _task_id, paths in delivery.sent for path in paths]
            self.assertIn(str(saved.resolve()), sent_paths)
            self.assertTrue(delivery.reporter.finalized)

    async def test_short_answer_writes_no_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = SimpleNamespace(
                id=8, owner_id="42", chat_id=99, prompt="do the work",
                execution_mode=None, attachments=(), is_recurring=False,
                status="queued",
            )
            repository = FakeRepository(task)
            attachments = TmpAttachmentStore(tmp)
            agent = FakeAgentService([{"parts": [{"type": "text", "text": "done"}]}])
            delivery = FakeDelivery()
            service = TaskExecutionService(
                repository, agent, attachments, audit_write=lambda *a, **k: None)
            await service.execute(task, delivery)
            self.assertFalse((Path(tmp) / "out-8" / "answer-full.md").exists())


if __name__ == "__main__":
    unittest.main()
