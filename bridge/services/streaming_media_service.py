"""Chunked primitives used by archive/media pipelines without buffering payloads."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bridge.infrastructure.storage.streaming import CancellationToken, StreamResult, copy_stream


class StreamingMediaService:
    def __init__(self, *, chunk_size: int = 1024 * 1024) -> None:
        self.chunk_size = max(64 * 1024, int(chunk_size))

    def iter_file(self, path: Path, *, cancellation: CancellationToken | None = None) -> Iterator[bytes]:
        with path.open("rb") as source:
            while True:
                if cancellation:
                    cancellation.raise_if_cancelled()
                chunk = source.read(self.chunk_size)
                if not chunk:
                    return
                yield chunk

    def copy_media(self, source: Path, destination: Path, *,
                   cancellation: CancellationToken | None = None) -> StreamResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with source.open("rb") as reader, destination.open("wb") as writer:
                return copy_stream(reader, writer, chunk_size=self.chunk_size, cancellation=cancellation)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def copy_archive_member(self, source, destination: Path, *,
                            cancellation: CancellationToken | None = None) -> StreamResult:
        """Copy an already-validated archive member stream; extraction policy is T13."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("wb") as writer:
                return copy_stream(source, writer, chunk_size=self.chunk_size, cancellation=cancellation)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
