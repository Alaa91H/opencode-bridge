"""Durable metadata/reference store for content-addressed attachments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .sqlite import BridgeDatabase


@dataclass(frozen=True)
class AttachmentReference:
    id: int
    sha256: str
    storage_key: str
    owner_id: str
    size_bytes: int
    scan_state: str


class AttachmentMetadataStore:
    def __init__(self, database: BridgeDatabase) -> None:
        self.database = database

    async def add_reference(self, *, sha256: str, storage_key: str, size_bytes: int,
                            owner_id: str, task_id: int | None = None,
                            file_id: str | None = None, file_unique_id: str | None = None,
                            claimed_mime: str | None = None, detected_mime: str | None = None,
                            retention_until: str | None = None, scan_state: str = "pending") -> AttachmentReference:
        now = datetime.now(timezone.utc).isoformat()
        db = await self.database.connect()
        async with self.database.transaction(immediate=True):
            await db.execute(
                """INSERT INTO storage_blobs(sha256,storage_key,size_bytes,reference_count,created_at,last_referenced_at)
                   VALUES(?,?,?,0,?,?)
                   ON CONFLICT(sha256) DO UPDATE SET last_referenced_at=excluded.last_referenced_at""",
                (sha256, storage_key, size_bytes, now, now),
            )
            cursor = await db.execute(
                """INSERT INTO attachment_refs(blob_sha256,owner_id,task_id,file_id,file_unique_id,
                   claimed_mime,detected_mime,size_bytes,retention_until,scan_state,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (sha256, owner_id, task_id, file_id, file_unique_id, claimed_mime,
                 detected_mime, size_bytes, retention_until, scan_state, now),
            )
            await db.execute(
                "UPDATE storage_blobs SET reference_count=reference_count+1,last_referenced_at=? WHERE sha256=?",
                (now, sha256),
            )
            ref_id = int(cursor.lastrowid)
        return AttachmentReference(ref_id, sha256, storage_key, owner_id, size_bytes, scan_state)

    async def release_reference(self, reference_id: int, owner_id: str) -> tuple[str, str, int] | None:
        db = await self.database.connect()
        async with self.database.transaction(immediate=True):
            async with db.execute(
                """SELECT r.blob_sha256,b.storage_key FROM attachment_refs r
                   JOIN storage_blobs b ON b.sha256=r.blob_sha256
                   WHERE r.id=? AND r.owner_id=?""", (reference_id, owner_id)
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            sha256, storage_key = str(row[0]), str(row[1])
            await db.execute("DELETE FROM attachment_refs WHERE id=?", (reference_id,))
            await db.execute(
                "UPDATE storage_blobs SET reference_count=MAX(reference_count-1,0) WHERE sha256=?",
                (sha256,),
            )
            async with db.execute("SELECT reference_count FROM storage_blobs WHERE sha256=?", (sha256,)) as cursor:
                count = int((await cursor.fetchone())[0])
        return sha256, storage_key, count

    async def unreferenced_blobs(self) -> list[tuple[str, str]]:
        db = await self.database.connect()
        async with db.execute(
            "SELECT sha256,storage_key FROM storage_blobs WHERE reference_count=0"
        ) as cursor:
            return [(str(row[0]), str(row[1])) for row in await cursor.fetchall()]

    async def forget_blob(self, sha256: str) -> None:
        db = await self.database.connect()
        async with self.database.transaction(immediate=True):
            await db.execute("DELETE FROM storage_blobs WHERE sha256=? AND reference_count=0", (sha256,))

    async def expired_references(self, now_iso: str) -> list[tuple[int, str]]:
        db = await self.database.connect()
        async with db.execute(
            """SELECT id,owner_id FROM attachment_refs
               WHERE retention_until IS NOT NULL AND retention_until<=?""", (now_iso,)
        ) as cursor:
            return [(int(row[0]), str(row[1])) for row in await cursor.fetchall()]
