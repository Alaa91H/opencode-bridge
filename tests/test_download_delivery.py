"""Simulated Telegram sends check part ordering, sizes and cleanup."""
from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from bridge.services.download_service import DownloadRecord
from bridge.telegram.download_delivery import deliver_download


class DownloadDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_splits_and_cleans_temporary_parts(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sample.mp4"
            source.write_bytes(b"ABCDEFGHIJ")
            now = datetime.now(UTC)
            record = DownloadRecord(
                id="abc", owner_id="owner", filename="sample.mp4",
                path=str(source), size_bytes=10, mime="video/mp4",
                source_url="https://example.com/video", created_at=now.isoformat(),
                expires_at=(now + timedelta(days=3)).isoformat(), direct_send_limit=4)
            payloads = []
            bot = AsyncMock()
            async def capture(*, chat_id, document, filename):
                payloads.append((chat_id, filename, document.read()))
            bot.send_document.side_effect = capture
            count = await deliver_download(bot, 42, record)
            self.assertEqual(count, 3)
            self.assertEqual([x[-1] for x in payloads], [b"ABCD", b"EFGH", b"IJ"])
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    async def test_single_file_is_sent_without_splitting(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "a.jpg"
            source.write_bytes(b"abcd")
            now = datetime.now(UTC)
            record = DownloadRecord(
                id="a", owner_id="owner", filename="a.jpg", path=str(source),
                size_bytes=4, mime="image/jpeg", source_url="https://example.com/a.jpg",
                created_at=now.isoformat(), expires_at=now.isoformat(),
                direct_send_limit=50)
            bot = AsyncMock()
            self.assertEqual(await deliver_download(bot, 12, record), 1)
            bot.send_document.assert_awaited_once()
