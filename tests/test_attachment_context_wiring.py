"""Attachment representations are wired into the prompt and the transport parts."""

# Cross-test fixture imports use a local testing-only namespace.
# ruff: noqa: I001

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from bridge.services.attachment_context_service import (
    AttachmentContextService,
    DerivedRepresentation,
)
from bridge.services.task_execution_service import TaskExecutionService
from test_v2_task_execution_service import (
    FakeAgentService,
    FakeAttachmentStore,
    FakeDelivery,
    FakeRepository,
)


class StoredFile:
    """A validated attachment that behaves like ``StoredAttachment``."""

    def __init__(self, path: Path, mime: str, *, visible: bool) -> None:
        self.path = str(path)
        self.filename = path.name
        self.mime = mime
        self.size = path.stat().st_size
        self.kind = "document"
        self._visible = visible

    def to_message_part(self):
        return {"type": "file", "url": Path(self.path).as_uri(), "mime": self.mime}

    def is_direct_model_visible(self):
        return self._visible


class ScriptedContext:
    """Stands in for the real service to isolate the wiring."""

    def __init__(self, block: str = "", items=(), raises: Exception | None = None) -> None:
        self.block = block
        self.items = list(items)
        self.raises = raises
        self.calls: list[tuple] = []

    def build(self, attachments, *, query):
        self.calls.append((tuple(attachments), query))
        if self.raises is not None:
            raise self.raises
        return self.block, self.items


class RecordingAttachmentStore(FakeAttachmentStore):
    def __init__(self, attachments) -> None:
        super().__init__()
        self.attachments = attachments

    def validate_input_records(self, records):
        return list(self.attachments)


def _task():
    return SimpleNamespace(
        id=11,
        owner_id="42",
        chat_id=99,
        prompt="لخّص محتوى الملف المرفق",
        execution_mode=None,
        attachments=({"path": "unused"},),
        is_recurring=False,
        status="queued",
    )


class ScriptedMedia:
    """Stands in for MediaAnalysisService so no host tool is ever executed."""

    def __init__(self, block: str = "", items=(), raises: Exception | None = None) -> None:
        self.block = block
        self.items = list(items)
        self.raises = raises
        self.calls: list[str] = []

    async def represent_for_prompt(self, attachments, *, query):
        self.calls.append(query)
        if self.raises is not None:
            raise self.raises
        return self.block, self.items


class DerivedContextWiringTests(unittest.IsolatedAsyncioTestCase):
    async def _execute(self, attachments, context, audits, media=None):
        task = _task()
        repository = FakeRepository(task)
        store = RecordingAttachmentStore(attachments)
        agent = FakeAgentService([{"parts": [{"type": "text", "text": "ok"}]}])
        service = TaskExecutionService(
            repository,
            agent,
            store,
            audit_write=lambda *args, **kwargs: audits.append((args, kwargs)),
            derived_context=context,
            media_analysis=media or ScriptedMedia(),
        )
        await service.execute(task, FakeDelivery())
        return agent.calls[0]

    async def test_represented_attachment_is_inlined_and_not_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hidden = root / "script.py"
            hidden.write_text("print('خطة الترحيل')\n", encoding="utf-8")
            visible = root / "photo.png"
            visible.write_bytes(b"\x89PNG\r\n\x1a\n")
            attachments = [
                StoredFile(hidden, "application/octet-stream", visible=False),
                StoredFile(visible, "image/png", visible=True),
            ]
            audits: list = []
            call = await self._execute(
                attachments,
                ScriptedContext(
                    "### script.py (text_excerpt)\nprint('خطة الترحيل')",
                    [DerivedRepresentation("script.py", "text_excerpt", "print('خطة الترحيل')")],
                ),
                audits,
            )
        self.assertIn("print('خطة الترحيل')", call["prompt"])
        self.assertIn("بيانات غير موثوقة", call["prompt"] + " ")  # tail note or path note
        # Only the image travels as a raw part; the excerpt replaced the script.
        self.assertEqual(len(call["parts"]), 1)
        self.assertEqual(call["parts"][0]["mime"], "image/png")
        self.assertTrue(any(args[0] == "attachment_context_derived" for args, _ in audits))
        details = next(
            kwargs["details"] for args, kwargs in audits if args[0] == "attachment_context_derived"
        )
        self.assertEqual(details["strategies"], ["text_excerpt"])

    async def test_oversized_visible_file_is_replaced_by_its_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            huge = root / "dump.txt"
            huge.write_text("سطر\n" * 1000, encoding="utf-8")
            attachments = [StoredFile(huge, "text/plain", visible=True)]
            call = await self._execute(
                attachments,
                ScriptedContext(
                    "### dump.txt (text_excerpt)\nسطر",
                    [DerivedRepresentation("dump.txt", "text_excerpt", "سطر")],
                ),
                [],
            )
        # Uploading a multi-megabyte dump burns the request for nothing; the
        # bounded excerpt is what reaches the model, and the path stays listed.
        self.assertEqual(call["parts"], [])
        self.assertIn("### dump.txt (text_excerpt)", call["prompt"])
        self.assertIn(str(huge), call["prompt"])

    async def test_visible_small_attachments_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "readme.md"
            readme.write_text("مرحبا", encoding="utf-8")
            attachments = [StoredFile(readme, "text/markdown", visible=True)]
            call = await self._execute(attachments, ScriptedContext(), [])
        self.assertEqual(len(call["parts"]), 1)
        self.assertNotIn("text_excerpt", call["prompt"])

    async def test_failure_degrades_to_the_previous_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("a.txt", "x")
            attachments = [StoredFile(archive, "application/zip", visible=False)]
            call = await self._execute(
                attachments,
                ScriptedContext(raises=RuntimeError("boom")),
                [],
            )
        self.assertEqual(call["parts"], [])
        self.assertIn("bundle.zip", call["prompt"])

    async def test_pdf_text_block_is_inlined_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "report.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            image = root / "chart.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            attachments = [
                StoredFile(pdf, "application/pdf", visible=False),
                StoredFile(image, "image/png", visible=True),
            ]
            audits: list = []
            media = ScriptedMedia(
                "### report.pdf (pdf_text)\nنتائج التقرير",
                [DerivedRepresentation("report.pdf", "pdf_text", "نتائج التقرير")],
            )
            call = await self._execute(attachments, ScriptedContext(), audits, media)
        self.assertIn("نتائج التقرير", call["prompt"])
        self.assertEqual(media.calls, ["لخّص محتوى الملف المرفق"])
        self.assertEqual(len(call["parts"]), 1)
        self.assertEqual(call["parts"][0]["mime"], "image/png")
        details = next(
            kwargs["details"] for args, kwargs in audits if args[0] == "attachment_context_derived"
        )
        self.assertEqual(details["strategies"], ["pdf_text"])

    async def test_media_failure_degrades_to_the_path_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "report.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            call = await self._execute(
                [StoredFile(pdf, "application/pdf", visible=False)],
                ScriptedContext(),
                [],
                ScriptedMedia(raises=RuntimeError("boom")),
            )
        self.assertNotIn("pdf_text", call["prompt"])
        self.assertIn("report.pdf", call["prompt"])
        self.assertEqual(call["parts"], [])

    async def test_real_service_end_to_end_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("notes/readme.txt", "محتوى")
                handle.writestr("notes/data.csv", "a,b")
            attachments = [StoredFile(archive, "application/zip", visible=False)]
            call = await self._execute(
                attachments, AttachmentContextService(), []
            )
        self.assertIn("notes/readme.txt", call["prompt"])
        self.assertIn("zip_manifest", call["prompt"])
        self.assertEqual(call["parts"], [])


if __name__ == "__main__":
    unittest.main()
