from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.storage.streaming import CancellationToken, StreamCancelled
from bridge.services.streaming_media_service import StreamingMediaService


class CancellingReader(io.BytesIO):
    def __init__(self, payload: bytes, token: CancellationToken):
        super().__init__(payload)
        self.token = token
        self.reads = 0

    def read(self, size=-1):
        if size < 0:
            raise AssertionError("unbounded read is forbidden")
        self.reads += 1
        data = super().read(size)
        if self.reads == 2:
            self.token.cancel()
        return data


class StreamingMediaTests(unittest.TestCase):
    def test_iter_file_never_uses_unbounded_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "media.bin"
            path.write_bytes(b"x" * 400_000)
            service = StreamingMediaService(chunk_size=65536)
            chunks = list(service.iter_file(path))
            self.assertEqual(sum(map(len, chunks)), 400_000)
            self.assertTrue(all(len(chunk) <= 65536 for chunk in chunks))

    def test_archive_copy_cancellation_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            token = CancellationToken()
            source = CancellingReader(b"a" * 300_000, token)
            output = Path(temp) / "member.bin"
            service = StreamingMediaService(chunk_size=65536)
            with self.assertRaises(StreamCancelled):
                service.copy_archive_member(source, output, cancellation=token)
            self.assertFalse(output.exists())

    def test_media_copy_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.bin"
            output = root / "nested" / "output.bin"
            source.write_bytes(b"m" * 700_000)
            result = StreamingMediaService(chunk_size=131072).copy_media(source, output)
            self.assertEqual(result.bytes_copied, 700_000)
            self.assertEqual(output.read_bytes(), source.read_bytes())
