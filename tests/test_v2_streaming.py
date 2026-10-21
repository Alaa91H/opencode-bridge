from __future__ import annotations

import asyncio
import hashlib
import tracemalloc
import unittest

from bridge.infrastructure.storage.streaming import (
    CancellationToken,
    StreamCancelled,
    copy_stream,
    copy_stream_async,
)


class VirtualLargeReader:
    def __init__(self, total: int):
        self.remaining = total
        self.max_requested = 0

    def read(self, size: int):
        self.max_requested = max(self.max_requested, size)
        if self.remaining <= 0:
            return b""
        amount = min(size, self.remaining)
        self.remaining -= amount
        return b"x" * amount


class CountingWriter:
    def __init__(self):
        self.total = 0

    def write(self, chunk):
        self.total += len(chunk)


class AsyncReader:
    def __init__(self, chunks: int, size: int):
        self.remaining = chunks
        self.size = size

    async def read(self, requested: int):
        await asyncio.sleep(0)
        if not self.remaining:
            return b""
        self.remaining -= 1
        return b"a" * min(requested, self.size)


class SlowAsyncWriter:
    def __init__(self):
        self.total = 0

    async def write(self, chunk):
        await asyncio.sleep(0.001)
        self.total += len(chunk)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    def test_virtual_multi_gigabyte_copy_has_bounded_peak_memory(self):
        total = 3 * 1024 * 1024 * 1024
        source = VirtualLargeReader(total)
        target = CountingWriter()
        tracemalloc.start()
        result = copy_stream(source, target, chunk_size=1024 * 1024)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(result.bytes_copied, total)
        self.assertEqual(target.total, total)
        self.assertLessEqual(source.max_requested, 1024 * 1024)
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_sync_cancellation_stops_between_chunks(self):
        token = CancellationToken()
        seen = 0
        def on_chunk(_):
            nonlocal seen
            seen += 1
            if seen == 3:
                token.cancel()
        with self.assertRaises(StreamCancelled):
            copy_stream(VirtualLargeReader(100 * 1024 * 1024), CountingWriter(),
                        cancellation=token, on_chunk=on_chunk)
        self.assertEqual(seen, 3)

    async def test_async_backpressure_and_hashing(self):
        source = AsyncReader(12, 128 * 1024)
        target = SlowAsyncWriter()
        result = await copy_stream_async(source, target, chunk_size=128 * 1024, max_in_flight_chunks=2)
        expected = hashlib.sha256(b"a" * (12 * 128 * 1024)).hexdigest()
        self.assertEqual(result.sha256, expected)
        self.assertEqual(result.bytes_copied, target.total)
        self.assertEqual(result.chunks, 12)

    async def test_async_cancellation_cleans_producer(self):
        token = CancellationToken(cancelled=True)
        with self.assertRaises(StreamCancelled):
            await copy_stream_async(AsyncReader(100, 65536), SlowAsyncWriter(), cancellation=token)
