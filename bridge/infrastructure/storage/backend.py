"""Storage backend contract for attachment blobs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class StorageObject:
    key: str
    size: int
    sha256: str


class StorageBackend(ABC):
    @abstractmethod
    def put_stream(self, source: BinaryIO, *, sha256: str | None = None) -> StorageObject:
        """Persist a stream and return its content-addressed object."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Open an object for streaming reads."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return whether an object exists."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete an object if present."""

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """Return a direct local path when the backend supports it."""
