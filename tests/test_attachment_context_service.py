"""Bounded local representations for attachments the model cannot read."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from bridge.domain.attachments.intelligence import AttachmentIntelligenceRouter
from bridge.infrastructure.media.archives import ArchiveLimits, UnsafeArchive, zip_manifest
from bridge.services.attachment_context_service import AttachmentContextService


def _attachment(tmp: Path | str, name: str, *, mime: str, size: int | None = None,
                visible: bool = False) -> SimpleNamespace:
    path = Path(tmp) / name
    return SimpleNamespace(
        path=str(path),
        filename=name,
        mime=mime,
        size=path.stat().st_size if size is None else size,
        is_direct_model_visible=lambda: visible,
    )


class OrderedSelectionTests(unittest.TestCase):
    def test_relevance_selection_keeps_document_order(self) -> None:
        chunks = [
            "الفصل الأول: مقدمة",
            "الفصل الثاني: ترحيل قاعدة البيانات وsqlite",
            "الفصل الثالث: الاختبارات",
            "الفصل الرابع: رجوع الإصدار مع sqlite",
        ]
        selected = AttachmentIntelligenceRouter.select_ordered_chunks(
            chunks, "sqlite", limit=2
        )
        self.assertEqual(selected, [chunks[1], chunks[3]])

    def test_fallbacks_are_the_leading_chunks(self) -> None:
        chunks = ["a", "b", "c"]
        self.assertEqual(
            AttachmentIntelligenceRouter.select_ordered_chunks(chunks, "", limit=2), ["a", "b"]
        )
        self.assertEqual(
            AttachmentIntelligenceRouter.select_ordered_chunks(chunks, "zzz", limit=2),
            ["a", "b"],
        )
        self.assertEqual(AttachmentIntelligenceRouter.select_ordered_chunks([], "x"), [])
        self.assertEqual(AttachmentIntelligenceRouter.select_ordered_chunks(chunks, "x", limit=0), [])


class ClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AttachmentContextService()

    def test_text_detection_covers_octet_stream_names(self) -> None:
        self.assertTrue(self.service.is_text_like(Path("a.md"), "application/octet-stream"))
        self.assertTrue(self.service.is_text_like(Path("a.PY"), "application/octet-stream"))
        self.assertTrue(self.service.is_text_like(Path("a.bin"), "text/plain; charset=utf-8"))
        self.assertFalse(self.service.is_text_like(Path("a.exe"), "application/octet-stream"))
        self.assertFalse(self.service.is_text_like(Path("a.pdf"), "application/pdf"))

    def test_archive_detection(self) -> None:
        self.assertTrue(self.service.is_archive(Path("a.zip"), "application/octet-stream"))
        self.assertTrue(self.service.is_archive(Path("a.bin"), "application/zip"))
        self.assertFalse(self.service.is_archive(Path("a.txt"), "text/plain"))

    def test_representation_only_for_unreadable_or_oversized(self) -> None:
        small_text = SimpleNamespace(path="/tmp/a.txt", mime="text/plain", size=10)
        self.assertTrue(
            self.service.needs_representation(small_text, direct_visible=False)
        )
        self.assertFalse(
            self.service.needs_representation(small_text, direct_visible=True)
        )
        big_text = SimpleNamespace(path="/tmp/b.txt", mime="text/plain", size=99 * 1024 * 1024)
        self.assertTrue(self.service.needs_representation(big_text, direct_visible=True))
        image = SimpleNamespace(path="/tmp/c.png", mime="image/png", size=10)
        self.assertFalse(self.service.needs_representation(image, direct_visible=True))
        self.assertFalse(
            self.service.needs_representation(
                SimpleNamespace(mime="text/plain"), direct_visible=True
            )
        )


class TextExcerptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AttachmentContextService()

    def test_short_file_is_inlined_whole(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("سطر أول\nسطر ثانٍ\n", encoding="utf-8")
            item = self.service.text_excerpt(path, query="سطر")
        self.assertEqual(item.strategy, "text_excerpt")
        self.assertIn("سطر ثانٍ", item.body)
        self.assertFalse(item.truncated)

    def test_long_file_keeps_document_order_and_respects_the_budget(self) -> None:
        head = "الفصل الأول: مقدمة المشروع sqlite"
        anchor = "الفصل الأخير: sqlite migration migration"
        # Bounded so the whole file stays inside MAX_READ_BYTES (bytes, not
        # characters) while still exceeding MAX_PER_FILE_CHARS: the anchor at
        # the end must actually be read for the ordering check to mean anything.
        filler_line = "حشو " * 100
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.txt"
            path.write_text(
                "\n".join([head, *[filler_line] * 100, anchor]), encoding="utf-8"
            )
            item = self.service.text_excerpt(path, query="sqlite migration")
        self.assertTrue(item.truncated)
        self.assertLessEqual(len(item.body), self.service.MAX_PER_FILE_CHARS)
        self.assertIn(self.service.TRUNCATION_MARKER, item.body)
        # The later chunk scores higher, so a relevance-ranked selection would
        # print it first; document order must win.
        self.assertLess(item.body.index(head), item.body.index(anchor))

    def test_read_is_bounded_even_for_huge_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "huge.txt"
            with path.open("w", encoding="utf-8") as handle:
                handle.write("أ" * (self.service.MAX_READ_BYTES + 5000))
            text, truncated = self.service.read_text(path)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text.encode("utf-8")), self.service.MAX_READ_BYTES + 8)

    def test_undecodable_bytes_do_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weird.txt"
            path.write_bytes(bytes(range(256)) * 10)
            item = self.service.text_excerpt(path, query="x")
        self.assertIsInstance(item.body, str)


class ArchiveManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AttachmentContextService()

    def _zip(self, tmp: Path, count: int) -> Path:
        path = tmp / "bundle.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for index in range(count):
                archive.writestr(f"dir/file{index}.txt", "x" * 10)
        return path

    def test_manifest_lists_entries_and_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(Path(tmp), 3)
            item = self.service.archive_manifest(path)
        self.assertEqual(item.strategy, "zip_manifest")
        self.assertIn("3 عنصرًا", item.body)
        self.assertIn("dir/file0.txt", item.body)
        self.assertFalse(item.truncated)

    def test_long_manifest_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(Path(tmp), self.service.MAX_ARCHIVE_ENTRIES + 25)
            item = self.service.archive_manifest(path)
        self.assertTrue(item.truncated)
        self.assertIn("عنصرًا آخر", item.body)

    def test_unsafe_archive_is_skipped_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._zip(Path(tmp), 4)
            strict = AttachmentContextService(archive_limits=ArchiveLimits(max_files=1))
            self.assertIsNone(strict.represent(
                _attachment(tmp, "bundle.zip", mime="application/zip")))
            with self.assertRaises(UnsafeArchive):
                zip_manifest(path, limits=ArchiveLimits(max_files=1))

    def test_bad_zip_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.zip"
            path.write_bytes(b"not a zip at all")
            self.assertIsNone(self.service.represent(
                _attachment(tmp, "fake.zip", mime="application/zip")))


class BuildBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AttachmentContextService()

    def test_block_is_capped_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attachments = []
            for index in range(4):
                path = root / f"f{index}.txt"
                path.write_text("محتوى " * 6000, encoding="utf-8")
                attachments.append(_attachment(root, path.name, mime="application/octet-stream"))
            block, derived = self.service.build(attachments, query="محتوى")
        # Only whole bodies are rendered: a stub would make the caller drop the
        # real upload for nothing.
        self.assertEqual(len(derived), self.service.MAX_TOTAL_CHARS // self.service.MAX_PER_FILE_CHARS)
        self.assertLessEqual(len(block), self.service.MAX_TOTAL_CHARS + 400)
        self.assertIn("بيانات غير موثوقة", block)
        self.assertIn("خارج السياق", block)

    def test_file_budget_limits_inspected_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attachments = []
            for index in range(10):
                path = root / f"g{index}.txt"
                path.write_text("بيانات", encoding="utf-8")
                attachments.append(_attachment(root, path.name, mime="text/plain"))
            block, derived = self.service.build(attachments, query="")
        self.assertEqual(len(derived), self.service.MAX_FILES)
        self.assertTrue(block)

    def test_visible_and_small_attachments_produce_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "readme.md"
            path.write_text("مرحبا", encoding="utf-8")
            block, derived = self.service.build(
                [_attachment(root, "readme.md", mime="text/markdown", visible=True)],
                query="",
            )
        self.assertEqual(block, "")
        self.assertEqual(derived, [])

    def test_empty_input_is_a_no_op(self) -> None:
        self.assertEqual(self.service.build([], query="x"), ("", []))
        self.assertEqual(self.service.build(None, query="x"), ("", []))

    def test_missing_file_is_skipped(self) -> None:
        item = SimpleNamespace(
            path="/nonexistent/gone.txt", filename="gone.txt", mime="text/plain",
            size=10, is_direct_model_visible=lambda: False,
        )
        self.assertEqual(self.service.build([item], query="x"), ("", []))


if __name__ == "__main__":
    unittest.main()
