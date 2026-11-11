from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.freebuff.extractor import (
    clean_text,
    newest_export,
    parse_export_payload,
    redact_secrets,
)


class ExtractorTests(unittest.TestCase):
    def test_secrets_are_redacted(self) -> None:
        text = "key cb_abcdefghijk and sk-abcdefghijklmnop and Bearer abcdefghijklmnop"
        redacted = redact_secrets(text)
        self.assertNotIn("cb_abcdefghijk", redacted)
        self.assertNotIn("sk-abcdefghijklmnop", redacted)
        self.assertNotIn("Bearer abcdefghijklmnop", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_clean_text_strips_ansi_and_bounds_output(self) -> None:
        value = clean_text("\x1b[31mhello\x1b[0m\n\n\n\n" + "x" * 5000, max_chars=1000)
        self.assertNotIn("\x1b", value)
        self.assertLessEqual(len(value), 1000)

    def test_export_json_is_parsed_as_a_conversation(self) -> None:
        payload = [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "done", "completed": True},
        ]
        self.assertEqual(parse_export_payload(payload), "done")

    def test_newest_export_skips_bridge_internal_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            internal = workspace / ".bridge" / "prompts"
            internal.mkdir(parents=True)
            (internal / "task.md").write_text("internal", encoding="utf-8")
            self.assertIsNone(newest_export(workspace, since=0))
            exported = workspace / "conversation.json"
            exported.write_text("[]", encoding="utf-8")
            self.assertEqual(newest_export(workspace, since=0), exported)


if __name__ == "__main__":
    unittest.main()
