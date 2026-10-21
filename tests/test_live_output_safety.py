"""Self-contained SSE safety tests (no network/client dependencies required)."""
from __future__ import annotations

import unittest

from bridge.domain.tasks.live_output import AgentLiveAssembler, redact_output


class SafeLivePreviewTests(unittest.TestCase):
    def test_full_user_prompt_and_tool_payload_are_suppressed(self):
        relay = AgentLiveAssembler()
        data = "أنت عم تنفّذ طلب وصل من بوت تيليغرام مُصرَّح."
        relay.consume({"type": "message.updated", "properties": {
            "info": {"id": "u1", "role": "user"}}})
        self.assertEqual(relay.consume({"type": "message.part.updated", "properties": {
            "part": {"id": "up", "messageID": "u1", "type": "text", "text": data}}}), "")
        relay.consume({"type": "message.updated", "properties": {
            "info": {"id": "a1", "role": "assistant"}}})
        self.assertEqual(relay.consume({"type": "message.part.updated", "properties": {
            "part": {"id": "t1", "messageID": "a1", "type": "tool", "tool": "websearch",
                     "state": {"status": "completed", "output": "API_KEY=test-secret-from-tool"}}}}), "")

    def test_previews_require_verified_assistant_role_and_text_part(self):
        relay = AgentLiveAssembler()
        part = {"type": "message.part.updated", "properties": {
            "part": {"id": "p", "messageID": "a1", "type": "text", "text": "Answer"}}}
        self.assertEqual(relay.consume(part), "")
        self.assertEqual(relay.consume({"type": "message.updated", "properties": {
            "info": {"id": "a1", "role": "assistant"}}}), "")
        self.assertEqual(relay.consume(part), "Answer")
        self.assertEqual(relay.consume(part), "")

    def test_delta_before_part_metadata_is_buffered_but_not_leaked(self):
        relay = AgentLiveAssembler()
        relay.consume({"type": "message.updated", "properties": {
            "info": {"id": "a1", "role": "assistant"}}})
        self.assertEqual(relay.consume({"type": "message.part.delta", "properties": {
            "partID": "p", "field": "text", "delta": "ABC"}}), "")
        part = {"type": "message.part.updated", "properties": {
            "part": {"id": "p", "messageID": "a1", "type": "text", "text": "ABCDE"}}}
        self.assertEqual(relay.consume(part), "ABCDE")
        self.assertEqual(relay.consume(part), "")
        self.assertEqual(relay.consume({"type": "message.part.updated", "properties": {
            "part": {"id": "p", "messageID": "a1", "type": "reasoning", "text": "hidden"}}}), "")

    def test_synthetic_text_parts_never_emit_followup_deltas(self):
        relay = AgentLiveAssembler()
        relay.consume({"type": "message.updated", "properties": {
            "info": {"id": "a1", "role": "assistant"}}})
        self.assertEqual(relay.consume({"type": "message.part.updated", "properties": {
            "part": {"id": "synthetic-1", "messageID": "a1",
                     "type": "text", "synthetic": True, "text": "internal"}}}), "")
        self.assertEqual(relay.consume({"type": "message.part.delta", "properties": {
            "partID": "synthetic-1", "messageID": "a1",
            "field": "text", "delta": "private"}}), "")

    def test_tool_errors_are_suppressed_and_redaction_still_works(self):
        relay = AgentLiveAssembler()
        relay.consume({"type": "message.updated", "properties": {
            "info": {"id": "a1", "role": "assistant"}}})
        self.assertEqual(relay.consume({"type": "message.part.updated", "properties": {
            "part": {"id": "t1", "messageID": "a1", "type": "tool",
                     "state": {"status": "error", "error": "private crash details"}}}}), "")
        self.assertEqual(redact_output("Authorization: Bearer ABCdef0123456789"),
                         "Authorization: Bearer [REDACTED]")


class TokenShapeRedactionTests(unittest.TestCase):
    def test_bot_github_and_vendor_tokens_are_masked(self) -> None:
        bot_token = "123456:" + "A" * 35
        github_token = "ghp_" + "B" * 25
        vendor_key = "sk-" + "c" * 20
        for secret in (bot_token, github_token, vendor_key):
            with self.subTest(secret=secret[:12]):
                out = redact_output(f"use {secret} here")
                self.assertNotIn(secret, out)
                self.assertIn("[REDACTED]", out)

    def test_ordinary_text_is_untouched(self) -> None:
        text = "disk space is low, task completed, risk-free deploy at 12:00"
        self.assertEqual(redact_output(text), text)


if __name__ == "__main__":
    unittest.main()
