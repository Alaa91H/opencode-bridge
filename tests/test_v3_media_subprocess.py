"""Run a real subprocess extractor against a deterministic fixture, no web access."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bridge.services.download_service import DownloadError, DownloadService


class MediaDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_media_subprocess_and_three_day_expiry(self):
        if os.name != "posix":
            self.skipTest("POSIX executable fixture")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            exe = root / "fake-ytdlp"
            exe.write_text("#!/usr/bin/env python3\nfrom pathlib import Path\nPath('sample.mp4').write_bytes(b'video-data')\n", encoding="utf-8")
            exe.chmod(0o700)
            service = DownloadService(root / "downloads", ytdlp_path=str(exe),
                                      allow_private_hosts=True)
            record = await service.fetch("owner", "https://example.org/watch?v=1", media=True)
            self.assertEqual(Path(record.path).read_bytes(), b"video-data")
            self.assertEqual(record.mime, "video/mp4")
            self.assertEqual(datetime.fromisoformat(record.expires_at) -
                             datetime.fromisoformat(record.created_at), timedelta(hours=72))
            self.assertEqual(service.cleanup_expired(
                now=datetime.fromisoformat(record.created_at) + timedelta(hours=73))["removed"], 1)
            self.assertFalse(Path(record.path).exists())

    async def test_media_fails_before_subprocess_for_local_url(self):
        with tempfile.TemporaryDirectory() as folder:
            service = DownloadService(Path(folder), ytdlp_path="/nonexistent/ytdlp")
            with self.assertRaises(DownloadError):
                await service.fetch("owner", "http://127.0.0.1/private", media=True)

    def test_shared_storage_limit_is_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            service = DownloadService(Path(folder), max_total_bytes=40 * 1024**3)
            self.assertEqual(service.max_total_bytes, 20 * 1024**3)
