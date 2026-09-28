"""Content-addressed local attachment storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from .backend import StorageBackend, StorageObject


class LocalStorage(StorageBackend):
    def __init__(self, root: Path, *, chunk_size: int = 1024 * 1024) -> None:
        self.root = root.resolve()
        self.chunk_size = max(64 * 1024, int(chunk_size))
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("invalid content storage key")
        path = (self.root / key[:2] / key[2:4] / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("storage key escapes root")
        return path

    def put_stream(self, source: BinaryIO, *, sha256: str | None = None) -> StorageObject:
        digest = hashlib.sha256()
        size = 0
        fd, temp_name = tempfile.mkstemp(prefix=".incoming-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as target:
                while True:
                    chunk = source.read(self.chunk_size)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            actual = digest.hexdigest()
            if sha256 is not None and sha256.lower() != actual:
                raise ValueError("sha256 mismatch")
            destination = self._path(actual)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                os.unlink(temp_name)
            else:
                os.replace(temp_name, destination)
            return StorageObject(key=actual, size=size, sha256=actual)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def local_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.is_file() else None
