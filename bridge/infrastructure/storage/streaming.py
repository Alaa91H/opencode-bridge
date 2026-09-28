"""Bounded-memory streaming primitives."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import BinaryIO, Callable


class StreamCancelled(Exception):
    pass


@dataclass
class CancellationToken:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise StreamCancelled("stream cancelled")


@dataclass(frozen=True)
class StreamResult:
    bytes_copied: int
    sha256: str
    chunks: int


def copy_stream(source: BinaryIO, target: BinaryIO, *, chunk_size: int = 1024 * 1024,
                cancellation: CancellationToken | None = None,
                on_chunk: Callable[[int], None] | None = None) -> StreamResult:
    size = max(64 * 1024, int(chunk_size))
    digest = hashlib.sha256()
    total = chunks = 0
    while True:
        if cancellation:
            cancellation.raise_if_cancelled()
        chunk = source.read(size)
        if not chunk:
            break
        target.write(chunk)
        digest.update(chunk)
        total += len(chunk)
        chunks += 1
        if on_chunk:
            on_chunk(len(chunk))
    return StreamResult(total, digest.hexdigest(), chunks)


async def copy_stream_async(source, target, *, chunk_size: int = 1024 * 1024,
                            cancellation: CancellationToken | None = None,
                            max_in_flight_chunks: int = 2) -> StreamResult:
    """Async copy with a bounded queue providing producer/consumer backpressure."""
    size = max(64 * 1024, int(chunk_size))
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max(1, int(max_in_flight_chunks)))
    digest = hashlib.sha256()
    total = chunks = 0

    async def produce() -> None:
        while True:
            if cancellation:
                cancellation.raise_if_cancelled()
            chunk = await source.read(size)
            if not chunk:
                await queue.put(None)
                return
            await queue.put(chunk)

    producer = asyncio.create_task(produce())
    try:
        while True:
            if cancellation:
                cancellation.raise_if_cancelled()
            chunk = await queue.get()
            if chunk is None:
                break
            await target.write(chunk)
            digest.update(chunk)
            total += len(chunk)
            chunks += 1
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
    return StreamResult(total, digest.hexdigest(), chunks)
