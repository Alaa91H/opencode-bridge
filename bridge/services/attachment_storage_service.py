"""Attachment storage use cases independent from Telegram."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bridge.infrastructure.database.attachment_store import AttachmentMetadataStore
from bridge.infrastructure.storage.backend import StorageBackend


class AttachmentStorageService:
    def __init__(self, backend: StorageBackend, metadata: AttachmentMetadataStore) -> None:
        self.backend = backend
        self.metadata = metadata

    async def ingest_path(self, path: Path, *, owner_id: str, task_id: int | None = None,
                          file_id: str | None = None, file_unique_id: str | None = None,
                          claimed_mime: str | None = None, detected_mime: str | None = None,
                          retention_until: str | None = None, scan_state: str = "pending"):
        with path.open("rb") as source:
            blob = self.backend.put_stream(source)
        return await self.metadata.add_reference(
            sha256=blob.sha256, storage_key=blob.key, size_bytes=blob.size,
            owner_id=owner_id, task_id=task_id, file_id=file_id,
            file_unique_id=file_unique_id, claimed_mime=claimed_mime,
            detected_mime=detected_mime, retention_until=retention_until,
            scan_state=scan_state,
        )

    async def release(self, reference_id: int, owner_id: str) -> bool:
        released = await self.metadata.release_reference(reference_id, owner_id)
        if released is None:
            return False
        sha256, storage_key, count = released
        if count == 0:
            self.backend.delete(storage_key)
            await self.metadata.forget_blob(sha256)
        return True

    async def cleanup_expired(self, now: datetime | None = None) -> int:
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        expired = await self.metadata.expired_references(stamp)
        removed = 0
        for reference_id, owner_id in expired:
            removed += int(await self.release(reference_id, owner_id))
        return removed

    async def cleanup_orphans(self) -> int:
        removed = 0
        for sha256, storage_key in await self.metadata.unreferenced_blobs():
            self.backend.delete(storage_key)
            await self.metadata.forget_blob(sha256)
            removed += 1
        return removed
