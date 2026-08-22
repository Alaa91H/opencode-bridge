from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from attachments import AttachmentError, AttachmentStore, StoredAttachment, attachment_prompt_note
from opencode_client import extract_file_response


class AttachmentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "attachments"
        self.store = AttachmentStore(self.root, max_bytes=32)
        self.store.ensure_directories()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_input_record_must_remain_inside_managed_incoming_directory(self) -> None:
        incoming = self.store.incoming_directory("1") / "report.txt"
        incoming.write_text("safe", encoding="utf-8")
        record = StoredAttachment(
            path=str(incoming),
            filename="report.txt",
            mime="text/plain",
            size=incoming.stat().st_size,
            kind="document",
        ).to_record()
        self.assertEqual(self.store.validate_input_records([record])[0].filename, "report.txt")

        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        record["path"] = str(outside)
        with self.assertRaises(AttachmentError):
            self.store.validate_input_records([record])

    def test_output_collection_rejects_symlink_and_oversized_files(self) -> None:
        output = self.store.task_output_directory(7)
        allowed = output / "result.txt"
        allowed.write_text("good", encoding="utf-8")
        oversized = output / "large.bin"
        oversized.write_bytes(b"x" * 33)
        outside = Path(self.temp.name) / "outside.bin"
        outside.write_bytes(b"outside")
        (output / "link.bin").symlink_to(outside)

        self.assertEqual(self.store.collect_task_outputs(7), [allowed.resolve()])

    def test_prompt_note_limits_agent_file_delivery_to_task_directory(self) -> None:
        output = self.store.task_output_directory(9)
        note = attachment_prompt_note([], output)
        self.assertIn(str(output), note)
        self.assertIn("لا ترسل أي ملف من مسار آخر", note)

    def test_sticker_is_exposed_as_a_managed_file_type(self) -> None:
        from attachments import select_telegram_attachment

        sticker = SimpleNamespace(file_unique_id="abc", is_video=False, is_animated=False)
        message = SimpleNamespace(
            document=None,
            photo=None,
            video=None,
            audio=None,
            voice=None,
            animation=None,
            video_note=None,
            sticker=sticker,
        )
        _, kind, filename, mime = select_telegram_attachment(message)
        self.assertEqual(kind, "sticker")
        self.assertEqual(filename, "sticker_abc.webp")
        self.assertEqual(mime, "image/webp")


class OpenCodeFilePartTests(unittest.TestCase):
    def test_extract_file_response_keeps_only_well_formed_file_parts(self) -> None:
        response = {
            "parts": [
                {"type": "text", "text": "done"},
                {"type": "file", "url": "file:///tmp/report.pdf", "mime": "application/pdf", "filename": "report.pdf"},
                {"type": "file", "url": 1, "mime": "application/pdf"},
            ]
        }
        self.assertEqual(
            extract_file_response(response),
            [{"url": "file:///tmp/report.pdf", "mime": "application/pdf", "filename": "report.pdf"}],
        )


if __name__ == "__main__":
    unittest.main()
