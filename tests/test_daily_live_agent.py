"""End-to-end-ish contract tests for multi-day tasks and sanitized SSE output."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from bridge.domain.tasks.daily_continuation import continuation_delay_seconds, is_task_complete
from bridge.domain.tasks.live_output import AgentLiveAssembler, redact_output
from bridge.domain.tasks.retry_policy import next_daily_reset_utc
from bridge.services.task_execution_service import TaskExecutionService
from tests.test_v2_task_execution_service import FakeAttachmentStore, FakeAgentService, FakeDelivery


class DailyQuotaClockTests(unittest.TestCase):
    def test_utc_noon_on_both_sides_of_boundary(self):
        self.assertEqual(next_daily_reset_utc(datetime(2026, 10, 12, 11, 59, tzinfo=UTC)),
                         datetime(2026, 10, 12, 12, tzinfo=UTC))
        self.assertEqual(next_daily_reset_utc(datetime(2026, 10, 12, 12, tzinfo=UTC)),
                         datetime(2026, 10, 13, 12, tzinfo=UTC))
        self.assertEqual(continuation_delay_seconds(datetime(2026, 10, 12, 11, 59, tzinfo=UTC)), 60)

    def test_completion_needs_trailing_marker_not_just_any_mention(self):
        self.assertFalse(is_task_complete('Example: [[BRIDGE_TASK_COMPLETE]] is the marker.'))
        self.assertTrue(is_task_complete('All checks passed.\n[[BRIDGE_TASK_COMPLETE]]\n'))
        self.assertFalse(is_task_complete('Still working.'))


class LiveSseTests(unittest.TestCase):
    def test_delta_before_snapshot_and_snapshot_deduplication(self):
        relay = AgentLiveAssembler()
        delta = {"type": "message.part.delta", "properties": {
            "partID": "p1", "field": "text", "delta": "hello"}}
        partial = {"type": "message.part.updated", "properties": {
            "part": {"id": "p1", "type": "text", "text": ""}}}
        full = {"type": "message.part.updated", "properties": {
            "part": {"id": "p1", "type": "text", "text": "hello world"}}}
        self.assertEqual(relay.consume(delta), "")  # unknown: might be hidden reasoning
        self.assertEqual(relay.consume(partial), "hello")
        self.assertEqual(relay.consume(full), " world")
        self.assertEqual(relay.consume(full), "")

    def test_reasoning_is_never_forwarded_even_when_delta_comes_first(self):
        relay = AgentLiveAssembler()
        self.assertEqual(relay.consume({"type": "message.part.delta", "properties": {
            "partID": "hidden", "field": "text", "delta": "private thinking"}}), "")
        self.assertEqual(relay.consume({"type": "message.part.updated", "properties": {
            "part": {"id": "hidden", "type": "reasoning", "text": "private thinking"}}}), "")

    def test_tool_output_once_and_redacted(self):
        relay = AgentLiveAssembler()
        event = {"type": "message.part.updated", "properties": {"part": {
            "id": "tool1", "type": "tool", "tool": "bash", "state": {
                "status": "completed", "input": {"secret": "not-sent"},
                "output": "password=superSecretToken123"}}}}
        self.assertIn("[REDACTED]", relay.consume(event))
        self.assertEqual(relay.consume(event), "")
        self.assertEqual(redact_output("Authorization: Bearer ABCdef0123456789"),
                         "Authorization: Bearer [REDACTED]")


class DailyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_daily_turn_is_checkpointed_not_finished_and_completes_next_day(self):
        task = SimpleNamespace(id=44, owner_id="42", chat_id=42, prompt="Write program exactly",
                               execution_mode=None, attachments=(), status="running", is_recurring=False,
                               checkpoint={"direct_dispatch": True, "daily_continuation": True})

        class Repository:
            def __init__(self):
                self.retries = []
                self.finished = []
            async def get(self, id):
                return task
            async def save_checkpoint(self, id, checkpoint):
                task.checkpoint = dict(checkpoint)
            async def retry_or_dead_letter(self, id, reason, **kwargs):
                self.retries.append((reason, kwargs))
                task.checkpoint = dict(kwargs.get("checkpoint") or task.checkpoint)
                task.status = "retrying"
                return task
            async def finish(self, id, success, error=None):
                self.finished.append((success, error))
                task.status = "completed" if success else "failed"

        repo = Repository()
        agent = FakeAgentService([
            {"parts": [{"type": "text", "text": "Day 1: implemented module"}]},
            {"parts": [{"type": "text", "text": "Done and tested\n[[BRIDGE_TASK_COMPLETE]]"}]},
        ])
        delivery = FakeDelivery()
        service = TaskExecutionService(repo, agent, FakeAttachmentStore(), audit_write=lambda *a, **k: None)
        await service.execute(task, delivery)
        self.assertEqual(repo.finished, [])
        self.assertEqual(task.status, "retrying")
        self.assertIn("12:00 UTC", delivery.reporter.finalized[0][0])
        self.assertEqual(repo.retries[0][0], "daily_continuation")
        self.assertGreater(repo.retries[0][1]["retry_after"], 1)
        self.assertEqual(task.checkpoint["continuation_count"], 1)
        self.assertNotIn("message_id", task.checkpoint)
        first_prompt = agent.calls[0]["prompt"]
        self.assertTrue(first_prompt.startswith("Write program exactly"))
        self.assertNotIn("Research Mode", first_prompt)
        task.status = "running"
        await service.execute(task, delivery)
        self.assertEqual(repo.finished, [(True, None)])
        self.assertEqual(agent.calls[0]["session_id"], agent.calls[1]["session_id"])
        self.assertEqual(len(repo.retries), 1)
        self.assertNotIn("[[BRIDGE_TASK_COMPLETE]]", delivery.reporter.finalized[-1][0])
