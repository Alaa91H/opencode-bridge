"""File-size and SSRF admission tests for anonymous mirrors."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.services.download_service import DownloadError
from bridge.services.mirrors import PROVIDERS, MirrorService


class MirrorAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_local_or_metadata_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            service = MirrorService(Path(folder))
            with self.assertRaises(ValueError):
                await service.mirror_url("tmpfiles", "https://example.com")
            with self.assertRaises(DownloadError):
                await service.mirror_url("catbox", "http://127.0.0.1/private")
            with self.assertRaises(DownloadError):
                await service.mirror_url("tempfile", "http://169.254.169.254/metadata")

    async def test_outside_managed_tree_is_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "outside.txt"
            file.write_text("sensitive")
            service = MirrorService(root / "downloads")
            with self.assertRaises(ValueError):
                await service.mirror_file("catbox", file)

    def test_provider_limits_and_result_hosts(self):
        self.assertEqual(PROVIDERS["catbox"]["limit"], 200 * 1024 * 1024)
        self.assertEqual(PROVIDERS["tmpfiles"]["limit"], 100 * 1024 * 1024)
        with self.assertRaises(ValueError):
            MirrorService._check_result("https://example.org/a", "files.catbox.moe")
        self.assertEqual(
            MirrorService._check_result("https://files.catbox.moe/file.jpg",
                                        "files.catbox.moe"),
            "https://files.catbox.moe/file.jpg")
