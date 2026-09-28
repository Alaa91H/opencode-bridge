"""Optional S3/MinIO storage backend.

The dependency is intentionally optional; constructing this backend requires a
boto3-compatible client supplied by deployment code.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import BinaryIO

from .backend import StorageBackend, StorageObject


class S3Storage(StorageBackend):
    def __init__(self, client, bucket: str, *, prefix: str = "attachments", chunk_size: int = 1024 * 1024) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.chunk_size = max(64 * 1024, int(chunk_size))

    def _object_key(self, key: str) -> str:
        return f"{self.prefix}/{key[:2]}/{key}" if self.prefix else f"{key[:2]}/{key}"

    def put_stream(self, source: BinaryIO, *, sha256: str | None = None) -> StorageObject:
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
            result = copy_stream(source, staged, chunk_size=self.chunk_size)
            actual = result.sha256
            if sha256 is not None and sha256.lower() != actual:
                raise ValueError("sha256 mismatch")
            staged.seek(0)
            if not self.exists(actual):
                self.client.upload_fileobj(staged, self.bucket, self._object_key(actual))
        return StorageObject(actual, result.bytes_copied, actual)

    def open(self, key: str) -> BinaryIO:
        staged = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        self.client.download_fileobj(self.bucket, self._object_key(key), staged)
        staged.seek(0)
        return staged

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            return True
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))

    def local_path(self, key: str) -> Path | None:
        return None
