"""Deadline-bounded, async host media tools for the task path."""
from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.services.media_analysis_service import MediaAnalysisService


def _analysis(*chunks: str) -> SimpleNamespace:
    return SimpleNamespace(
        kind="pdf",
        detected_mime="application/pdf",
        artifacts=tuple(
            SimpleNamespace(kind="pdf_text_chunk", text=chunk, metadata={"offset": index * 100})
            for index, chunk in enumerate(chunks)
        ),
        metadata={"text_available": True},
    )


class FakePipeline:
    """Stands in for MediaPipeline so no host tool is ever executed."""

    def __init__(self, result=None, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.result = result
        self.delay = delay
        self.error = error
        self.calls: list[object] = []

    def analyze(self, media):
        self.calls.append(media)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def _pdf(tmp: Path, name: str = "doc.pdf") -> SimpleNamespace:
    path = tmp / name
    path.write_bytes(b"%PDF-1.7\n")
    return SimpleNamespace(
        path=str(path), filename=name, mime="application/pdf",
        size=path.stat().st_size, is_direct_model_visible=lambda: False,
    )


class PdfDetectionTests(unittest.TestCase):
    def test_only_pdfs_are_considered(self) -> None:
        self.assertTrue(MediaAnalysisService.is_pdf(SimpleNamespace(path="/a/b.pdf", mime="")))
        self.assertTrue(MediaAnalysisService.is_pdf(SimpleNamespace(path="/a/b.bin", mime="application/pdf")))
        self.assertTrue(MediaAnalysisService.is_pdf(
            SimpleNamespace(path="/a/b.PDF", mime="application/octet-stream")))
        self.assertFalse(MediaAnalysisService.is_pdf(SimpleNamespace(path="/a/b.zip", mime="application/zip")))
        self.assertFalse(MediaAnalysisService.is_pdf(SimpleNamespace(mime="application/pdf")))


class RepresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_are_inlined_in_document_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = FakePipeline(_analysis("صفحة أولى", "صفحة ثانية", "صفحة ثالثة"))
            service = MediaAnalysisService(pipeline=pipeline)
            block, items = await service.represent_for_prompt(
                [_pdf(Path(tmp))], query="صفحة"
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].strategy, "pdf_text")
        self.assertIn("صفحة أولى", block)
        self.assertLess(block.index("صفحة أولى"), block.index("صفحة ثالثة"))
        self.assertIn("بيانات غير موثوقة", block)
        self.assertEqual(len(pipeline.calls), 1)

    async def test_many_chunks_are_selected_and_capped(self) -> None:
        chunks = ["حشو " * 200 + f"الفقرة {index}" for index in range(12)]
        chunks[11] = "الفقرة الأخيرة تتحدث عن sqlite migration"
        with tempfile.TemporaryDirectory() as tmp:
            service = MediaAnalysisService(pipeline=FakePipeline(_analysis(*chunks)))
            block, items = await service.represent_for_prompt(
                [_pdf(Path(tmp))], query="sqlite migration"
            )
        self.assertTrue(items[0].truncated)
        self.assertLessEqual(len(items[0].body), service.limits.MAX_PER_FILE_CHARS)
        self.assertIn(service.limits.TRUNCATION_MARKER, items[0].body)
        self.assertIn("sqlite migration", block)

    async def test_scanned_pdf_without_text_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MediaAnalysisService(pipeline=FakePipeline(_analysis()))
            self.assertEqual(
                await service.represent_for_prompt([_pdf(Path(tmp))], query="x"), ("", [])
            )

    async def test_non_pdf_attachments_never_reach_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = FakePipeline(_analysis("نص"))
            service = MediaAnalysisService(pipeline=pipeline)
            other = SimpleNamespace(
                path=str(Path(tmp) / "a.zip"), filename="a.zip", mime="application/zip",
                size=10, is_direct_model_visible=lambda: False,
            )
            self.assertEqual(
                await service.represent_for_prompt([other], query="x"), ("", [])
            )
        self.assertEqual(pipeline.calls, [])

    async def test_tool_failure_is_never_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = FakePipeline(
                error=subprocess.CalledProcessError(1, ["pdftotext"])
            )
            service = MediaAnalysisService(pipeline=pipeline)
            self.assertEqual(
                await service.represent_for_prompt([_pdf(Path(tmp))], query="x"), ("", [])
            )

    async def test_timeout_error_is_contained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MediaAnalysisService(pipeline=FakePipeline(error=TimeoutError()))
            self.assertEqual(
                await service.represent_for_prompt([_pdf(Path(tmp))], query="x"), ("", [])
            )

    async def test_deadline_stops_waiting_on_a_wedged_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = MediaAnalysisService(
                pipeline=FakePipeline(_analysis("نص"), delay=1.0), deadline_seconds=0.1
            )
            started = time.monotonic()
            self.assertEqual(
                await service.represent_for_prompt([_pdf(Path(tmp))], query="x"), ("", [])
            )
            self.assertLess(time.monotonic() - started, 0.9)


if __name__ == "__main__":
    unittest.main()
