"""Regression tests for one-message Telegram chat delivery."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from progress import ProgressStore
from progress_reporter import LiveProgressReporter


class _Events:
    async def stream_events(self, session_id):
        assert session_id == "session-1"
        yield {"type": "message.updated", "properties": {
            "info": {"id": "assistant-1", "role": "assistant"}}}
        yield {"type": "message.part.updated", "properties": {
            "part": {"id": "tool-1", "messageID": "assistant-1",
                     "type": "tool", "tool": "websearch",
                     "state": {"status": "completed",
                               "output": "SECRET TOOL OUTPUT"}}}}
        yield {"type": "message.updated", "properties": {
            "info": {"id": "user-1", "role": "user"}}}
        yield {"type": "message.part.updated", "properties": {
            "part": {"id": "user-part", "messageID": "user-1",
                     "type": "text", "text": "HIDDEN PROMPT"}}}
        yield {"type": "message.part.updated", "properties": {
            "part": {"id": "reply-1", "messageID": "assistant-1",
                     "type": "text", "text": "جواب مختصر"}}}
        yield {"type": "session.idle", "properties": {"sessionID": "session-1"}}


class TelegramConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_opt_in_preview_uses_same_message_and_hides_internal_text(self):
        task = SimpleNamespace(
            id=7, owner_id="17", chat_id=99, status_message_id=None)
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=45)),
            edit_message_text=AsyncMock(),
        )
        queue = SimpleNamespace(
            set_status_message_id=AsyncMock(),
            update_activity=AsyncMock(),
        )
        reporter = LiveProgressReporter(task, bot, queue, ProgressStore())
        reporter._live_enabled = True
        await reporter.start()
        await reporter.consume_events(_Events(), "session-1")
        self.assertEqual(bot.send_message.await_count, 1)
        previews = [call.kwargs["text"] for call in bot.edit_message_text.await_args_list]
        self.assertTrue(any("جواب مختصر" in text for text in previews))
        self.assertFalse(any("SECRET TOOL OUTPUT" in text for text in previews))
        self.assertFalse(any("HIDDEN PROMPT" in text for text in previews))
        self.assertTrue(all(call.kwargs["message_id"] == 45
                            for call in bot.edit_message_text.await_args_list))
        await reporter.finalize_text("الجواب النهائي.")
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertEqual(bot.edit_message_text.await_args.kwargs["text"], "الجواب النهائي.")
        self.assertEqual(bot.edit_message_text.await_args.kwargs["message_id"], 45)

    async def test_default_mode_does_not_emit_live_tool_messages(self):
        task = SimpleNamespace(id=8, owner_id="17", chat_id=99, status_message_id=None)
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=46)),
            edit_message_text=AsyncMock(),
        )
        queue = SimpleNamespace(
            set_status_message_id=AsyncMock(),
            update_activity=AsyncMock(),
        )
        reporter = LiveProgressReporter(task, bot, queue, ProgressStore())
        reporter._live_enabled = False
        await reporter.start()
        await reporter.consume_events(_Events(), "session-1")
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertFalse(any("SECRET TOOL OUTPUT" in call.kwargs["text"]
                             for call in bot.edit_message_text.await_args_list))


if __name__ == "__main__":
    unittest.main()
