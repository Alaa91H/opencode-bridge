"""Regression tests for prompt-file intake (bridge/services/prompt_files.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.services.prompt_files import (
    build_instruction_body,
    is_prompt_filename,
    load_prompt_text,
)


class PromptFilenameTests(unittest.TestCase):
    def test_prompt_extensions_are_instruction_files(self) -> None:
        for name in ("brief.md", "notes.markdown", "prompt.txt", "cfg.json",
                     "vars.yaml", "vars.yml", "conf.toml", "job.prompt"):
            with self.subTest(name=name):
                self.assertTrue(is_prompt_filename(name))

    def test_media_and_binary_files_are_not_prompt_files(self) -> None:
        for name in ("photo.jpg", "clip.mp4", "sound.ogg", "doc.pdf",
                     "archive.zip", "report.bin"):
            with self.subTest(name=name):
                self.assertFalse(is_prompt_filename(name, "application/octet-stream"))

    def test_text_mime_counts_even_without_extension(self) -> None:
        self.assertTrue(is_prompt_filename("notes", "text/plain"))
        self.assertTrue(is_prompt_filename("data", "application/json"))
        self.assertFalse(is_prompt_filename("data", "application/octet-stream"))


class LoadPromptTextTests(unittest.TestCase):
    def test_bom_crlf_and_nul_are_normalised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.md"
            path.write_bytes(b"\xef\xbb\xbf#line one\x00\r\nline two\r\n")
            text, truncated = load_prompt_text(path)
            self.assertFalse(truncated)
            self.assertNotIn("\x00", text)
            self.assertNotIn("\r", text)
            self.assertIn("#line one", text)
            self.assertIn("line two", text)

    def test_long_file_is_truncated_with_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.md"
            path.write_text("word " * 5000, encoding="utf-8")
            text, truncated = load_prompt_text(path, max_chars=100)
            self.assertTrue(truncated)
            self.assertLessEqual(len(text), 100 + 100)
            self.assertIn("اقتطاع", text)


class BuildInstructionBodyTests(unittest.TestCase):
    def test_user_text_and_file_body_are_combined_with_priority_note(self) -> None:
        body = build_instruction_body("نفذ الملف", [("brief.md", "افعل كذا", False)])
        self.assertIn("نفذ الملف", body)
        self.assertIn("بداية الملف المرفق: brief.md", body)
        self.assertIn("افعل كذا", body)
        self.assertIn("نهاية الملف المرفق: brief.md", body)
        self.assertIn("أوامر مباشرة", body)

    def test_truncated_file_carries_a_note(self) -> None:
        body = build_instruction_body("", [("long.md", "نص", True)])
        self.assertIn("اقتُطع ذيل الملف الطويل", body)

    def test_plain_text_without_files_has_no_priority_note(self) -> None:
        body = build_instruction_body("سؤال عادي", [])
        self.assertEqual(body, "سؤال عادي")


if __name__ == "__main__":
    unittest.main()
