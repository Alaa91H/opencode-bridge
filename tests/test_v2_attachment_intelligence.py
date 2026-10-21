from __future__ import annotations

import unittest
from pathlib import Path

from bridge.domain.attachments.intelligence import AttachmentContext, AttachmentIntelligenceRouter


class AttachmentIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.router = AttachmentIntelligenceRouter()
        self.context = AttachmentContext(max_direct_bytes=100)

    def route(self, name, mime, size):
        return self.router.route(path=Path(name), mime=mime, size=size, context=self.context)

    def test_small_supported_is_direct(self):
        self.assertEqual(self.route("a.png", "image/png", 50).strategy, "direct")

    def test_large_text_is_indexed(self):
        route = self.route("a.txt", "text/plain", 101)
        self.assertEqual(route.strategy, "text_index")
        self.assertIn("selected_chunks", route.representations)

    def test_rich_media_and_archive_routes(self):
        self.assertEqual(self.route("a.pdf", "application/pdf", 101).strategy, "pdf")
        self.assertEqual(self.route("a.mp4", "video/mp4", 101).strategy, "video")
        self.assertEqual(self.route("a.wav", "audio/wav", 101).strategy, "audio")
        self.assertEqual(self.route("a.zip", "application/octet-stream", 101).strategy, "archive")

    def test_context_selects_relevant_chunks(self):
        chunks = ["database migration rollback", "cat picture", "sqlite migration integrity", "weather"]
        selected = self.router.select_text_chunks(chunks, "sqlite migration", limit=2)
        self.assertEqual(selected[0], "sqlite migration integrity")
        self.assertIn("database migration rollback", selected)
