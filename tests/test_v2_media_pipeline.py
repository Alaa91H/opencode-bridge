from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from bridge.domain.attachments.media_pipeline import MediaInput, MediaPipeline
from bridge.infrastructure.media.archives import ArchiveLimits, UnsafeArchive, extract_zip, zip_manifest
from bridge.infrastructure.media.processors import AudioProcessor, ImageProcessor, PdfProcessor, VideoProcessor
from bridge.infrastructure.media.tools import MediaToolCapabilities, detect_media_tools


NO_TOOLS = MediaToolCapabilities(None, None, None, None, None)


class MediaPipelineTests(unittest.TestCase):
    def test_tool_detection_only_uses_path_lookup(self):
        with patch("bridge.infrastructure.media.tools.shutil.which", return_value=None) as which:
            caps = detect_media_tools()
        self.assertFalse(caps.video)
        self.assertFalse(caps.ocr)
        self.assertFalse(caps.pdf)
        self.assertEqual(which.call_count, 5)

    def test_processors_degrade_without_optional_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = [
                ("x.png", "image/png", ImageProcessor(NO_TOOLS), "image"),
                ("x.pdf", "application/pdf", PdfProcessor(NO_TOOLS), "pdf"),
                ("x.mp4", "video/mp4", VideoProcessor(NO_TOOLS), "video"),
                ("x.wav", "audio/wav", AudioProcessor(NO_TOOLS), "audio"),
            ]
            for name, mime, processor, kind in cases:
                path = root / name
                path.write_bytes(b"payload")
                result = MediaPipeline([processor]).analyze(MediaInput(path, mime))
                self.assertEqual(result.kind, kind)

    def test_zip_lists_then_extracts_safe_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "safe.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("folder/a.txt", "hello")
            manifest = zip_manifest(archive)
            self.assertEqual([x.name for x in manifest], ["folder/a.txt"])
            files = extract_zip(archive, root / "out")
            self.assertEqual(files[0].read_text(), "hello")

    def test_zip_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")
            with self.assertRaises(UnsafeArchive):
                zip_manifest(archive)

    def test_zip_rejects_expansion_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "large.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
                zf.writestr("a.bin", b"x" * 1024)
            with self.assertRaises(UnsafeArchive):
                zip_manifest(archive, limits=ArchiveLimits(max_uncompressed_bytes=100))
