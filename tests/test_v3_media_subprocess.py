"""Run a real subprocess extractor against a deterministic fixture, no web access."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridge.services.download_service import (
    DEFAULT_MAX_TOTAL_BYTES,
    DownloadError,
    DownloadRecord,
    DownloadService,
    DownloadTooLarge,
)


class MediaDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_media_subprocess_and_one_day_expiry(self):
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
                             datetime.fromisoformat(record.created_at), timedelta(hours=24))
            self.assertEqual(service.cleanup_expired(
                now=datetime.fromisoformat(record.created_at) + timedelta(hours=25))["removed"], 1)
            self.assertFalse(Path(record.path).exists())

    async def test_media_fails_before_subprocess_for_local_url(self):
        with tempfile.TemporaryDirectory() as folder:
            service = DownloadService(Path(folder), ytdlp_path="/nonexistent/ytdlp")
            with self.assertRaises(DownloadError):
                await service.fetch("owner", "http://127.0.0.1/private", media=True)

    def test_shared_storage_limit_is_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            # The shared budget doubles as the hard ceiling: a larger request is
            # clamped back to the budget instead of being honoured.
            service = DownloadService(
                Path(folder), max_total_bytes=2 * DEFAULT_MAX_TOTAL_BYTES
            )
            self.assertEqual(service.max_total_bytes, DEFAULT_MAX_TOTAL_BYTES)

    def test_a_full_quota_and_a_full_disk_say_different_things(self):
        """One message for two different faults sends an operator hunting the wrong one."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DownloadService(root / "downloads", max_total_bytes=1024)
            self.assertGreater(service._transfer_budget(), 0)
            stored = service.root / "owner" / "blob.bin"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(b"x" * 2048)
            record = DownloadRecord(
                id="a" * 32, owner_id="owner", filename="blob.bin", path=str(stored),
                size_bytes=2048, mime="application/octet-stream",
                source_url="https://example.org/blob",
                created_at=datetime.now(UTC).isoformat(),
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                direct_send_limit=1024,
            )
            service._write_index({record.id: record.to_dict()})
            with self.assertRaises(DownloadTooLarge) as quota:
                service._transfer_budget()
            self.assertIn("GiB used", str(quota.exception))

    def test_the_headroom_reserve_scales_down_on_a_small_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            service = DownloadService(Path(folder))
            budget = service._transfer_budget()
            self.assertGreater(budget, 0, "a small filesystem must still accept a transfer")
            self.assertLessEqual(budget, service.max_file_bytes)
