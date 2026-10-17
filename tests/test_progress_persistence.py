"""Persisted activity writes are coalesced; terminal states always flush."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from progress import ProgressStore
from progress_reporter import LiveProgressReporter


def _reporter() -> tuple[LiveProgressReporter, AsyncMock]:
    task = SimpleNamespace(id=3, owner_id="17", chat_id=99, status_message_id=None)
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=5)),
        edit_message_text=AsyncMock(),
    )
    queue = SimpleNamespace(
        set_status_message_id=AsyncMock(),
        update_activity=AsyncMock(),
    )
    return LiveProgressReporter(task, bot, queue, ProgressStore()), queue


def _tool_event(status: str) -> dict:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {"type": "tool", "tool": "bash", "state": {"status": status}}
        },
    }


class PersistCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_of_records_writes_once(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()
        self.assertEqual(queue.update_activity.await_count, 1)
        for index in range(20):
            await reporter.record("tool_running", f"خطوة {index}")
        # 20 events, one database write: the trail is coalesced, not per-event.
        self.assertEqual(queue.update_activity.await_count, 1)
        self.assertTrue(reporter._persist_pending)
        self.assertEqual(len(reporter.progress.entries), 21)

    async def test_window_expiry_allows_the_next_write(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()
        reporter._last_persisted -= 3600.0
        await reporter.record("tool_completed", "خلص أمرًا على الخادم.")
        self.assertEqual(queue.update_activity.await_count, 2)
        self.assertFalse(reporter._persist_pending)
        # The flushed payload carries every coalesced entry, not just the last.
        payload = queue.update_activity.await_args.args[1]
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[-1]["message"], "خلص أمرًا على الخادم.")

    async def test_duplicate_events_are_still_deduplicated(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()
        reporter._last_persisted -= 3600.0
        await reporter.record("tool_completed", "خلص أمرًا على الخادم.")
        writes = queue.update_activity.await_count
        await reporter.record("tool_completed", "خلص أمرًا على الخادم.")
        self.assertEqual(queue.update_activity.await_count, writes)

    async def test_final_answer_forces_a_flush(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()
        for index in range(5):
            await reporter.record("tool_running", f"خطوة {index}")
        self.assertEqual(queue.update_activity.await_count, 1)
        await reporter.finalize_text("النتيجة النهائية.")
        self.assertEqual(queue.update_activity.await_count, 2)
        self.assertFalse(reporter._persist_pending)
        payload = queue.update_activity.await_args.args[1]
        self.assertEqual(payload[-1]["phase"], "completed")

    async def test_finish_forces_a_flush(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()
        await reporter.record("tool_error", "واجهت أداة مشكلة.")
        self.assertEqual(queue.update_activity.await_count, 1)
        await reporter.finish("failed", "فشل التنفيذ.")
        self.assertEqual(queue.update_activity.await_count, 2)
        self.assertEqual(queue.update_activity.await_args.args[1][-1]["phase"], "failed")


class StreamFlushTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_stream_flushes_once_at_the_end(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()

        async def stream(_session_id):
            for _ in range(6):
                yield _tool_event("running")
            yield {"type": "session.idle", "properties": {"sessionID": "s1"}}

        client = SimpleNamespace(stream_events=stream)
        await reporter.consume_events(client, "s1")
        # start() + one flush when the stream ends, whatever the event count.
        self.assertEqual(queue.update_activity.await_count, 2)
        payload = queue.update_activity.await_args.args[1]
        self.assertEqual(payload[-1]["phase"], "agent_idle")

    async def test_cancelled_stream_still_leaves_a_flushed_trail(self) -> None:
        reporter, queue = _reporter()
        await reporter.start()

        async def stream(_session_id):
            yield _tool_event("running")
            await asyncio.sleep(10)

        client = SimpleNamespace(stream_events=stream)
        task = asyncio.create_task(reporter.consume_events(client, "s1"))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertGreaterEqual(queue.update_activity.await_count, 1)


if __name__ == "__main__":
    unittest.main()
