"""Framework-independent attachment task orchestration.

Telegram download mechanics stay in the Telegram adapter. This service owns the
rules for validating, staging, merging, restoring, expiring, and queueing
attachment records.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from bridge.domain.policies import RequestGuard


class AttachmentStorePort(Protocol):
    def validate_input_records(self, records: list[dict[str, Any]]) -> list[Any]: ...
    def delete_input_records(self, records: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> int: ...


class MediaTaskRepository(Protocol):
    async def pop_pending_attachments(
        self,
        owner_id: str,
        chat_id: int,
    ) -> tuple[tuple[dict[str, Any], ...], bool]: ...

    async def stage_pending_attachments(
        self,
        owner_id: str,
        chat_id: int,
        attachments: list[dict[str, Any]],
        ttl_seconds: int,
        max_count: int,
        max_total_bytes: int,
    ) -> tuple[Any, tuple[dict[str, Any], ...]]: ...

    async def purge_expired_pending_attachments(self) -> tuple[dict[str, Any], ...]: ...

    async def enqueue(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        status_message_id: int | None = None,
        execution_mode: str | None = None,
    ) -> tuple[Any, int]: ...


class PendingAttachmentsExpired(ValueError):
    pass


@dataclass(frozen=True)
class QueuedMediaTask:
    task: Any
    attachments: tuple[dict[str, Any], ...]
    queue_position: int


@dataclass(frozen=True)
class StagedMediaBatch:
    batch: Any
    attachments: tuple[dict[str, Any], ...]


class MediaTaskService:
    def __init__(
        self,
        repository: MediaTaskRepository,
        attachment_store: AttachmentStorePort,
        guard: RequestGuard,
        *,
        pending_ttl_seconds: int,
        max_count: int,
        max_total_bytes: int,
    ) -> None:
        self.repository = repository
        self.attachment_store = attachment_store
        self.guard = guard
        self.pending_ttl_seconds = max(60, int(pending_ttl_seconds))
        self.max_count = max(1, int(max_count))
        self.max_total_bytes = max(1, int(max_total_bytes))

    @staticmethod
    def records_from_attachments(attachments: list[Any]) -> list[dict[str, Any]]:
        return [attachment.to_record() for attachment in attachments]

    async def _pop_pending_valid(
        self,
        owner_id: str,
        chat_id: int,
    ) -> tuple[dict[str, Any], ...]:
        records, expired = await self.repository.pop_pending_attachments(owner_id, chat_id)
        if expired:
            self.attachment_store.delete_input_records(records)
            raise PendingAttachmentsExpired("انتهت مهلة الملفات المعلّقة")
        return records

    async def stage_records(
        self,
        owner_id: str,
        chat_id: int,
        records: list[dict[str, Any]],
    ) -> StagedMediaBatch:
        self.attachment_store.validate_input_records(records)
        try:
            batch, expired_records = await self.repository.stage_pending_attachments(
                owner_id,
                chat_id,
                records,
                ttl_seconds=self.pending_ttl_seconds,
                max_count=self.max_count,
                max_total_bytes=self.max_total_bytes,
            )
        except Exception:
            # Only the newly supplied records are unowned when staging failed.
            self.attachment_store.delete_input_records(records)
            raise
        if expired_records:
            self.attachment_store.delete_input_records(expired_records)
        return StagedMediaBatch(batch=batch, attachments=tuple(batch.attachments))

    async def restore_pending(
        self,
        owner_id: str,
        chat_id: int,
        records: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> None:
        if not records:
            return
        await self.repository.stage_pending_attachments(
            owner_id,
            chat_id,
            list(records),
            ttl_seconds=self.pending_ttl_seconds,
            max_count=self.max_count,
            max_total_bytes=self.max_total_bytes,
        )

    async def take_pending(
        self,
        owner_id: str,
        chat_id: int,
    ) -> tuple[dict[str, Any], ...]:
        records = await self._pop_pending_valid(owner_id, chat_id)
        if records:
            self.attachment_store.validate_input_records(list(records))
        return records

    async def queue_records(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        records: list[dict[str, Any]],
        *,
        status_message_id: int | None = None,
        merge_pending: bool = True,
        execution_mode: str | None = None,
    ) -> QueuedMediaTask:
        pending: tuple[dict[str, Any], ...] = ()
        if merge_pending:
            try:
                pending = await self._pop_pending_valid(owner_id, chat_id)
            except PendingAttachmentsExpired:
                # Expired staged records are discarded, but a fresh upload in
                # this same request is still valid and may proceed.
                pending = ()

        combined = [*pending, *records]
        self.attachment_store.validate_input_records(combined)
        try:
            self.guard.ensure_allowed(prompt)
        except Exception:
            # A policy-rejected instruction must not leave a fresh or staged
            # attachment batch silently executable by a later unrelated text.
            self.attachment_store.delete_input_records(combined)
            raise

        try:
            task, position = await self.repository.enqueue(
                owner_id,
                chat_id,
                prompt,
                attachments=combined,
                status_message_id=status_message_id,
                execution_mode=execution_mode,
            )
        except Exception:
            # Restore only previously-staged records. Fresh records still
            # belong to the caller and can be cleaned/retried by the adapter.
            if pending:
                await self.restore_pending(owner_id, chat_id, pending)
            raise

        return QueuedMediaTask(
            task=task,
            attachments=tuple(combined),
            queue_position=position,
        )

    async def queue_taken_pending(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        records: tuple[dict[str, Any], ...],
        *,
        status_message_id: int | None = None,
    ) -> QueuedMediaTask:
        self.attachment_store.validate_input_records(list(records))
        try:
            self.guard.ensure_allowed(prompt)
        except Exception:
            # These records were explicitly waiting for the next instruction;
            # keep them pending when that instruction is rejected before queueing.
            await self.restore_pending(owner_id, chat_id, records)
            raise
        try:
            task, position = await self.repository.enqueue(
                owner_id,
                chat_id,
                prompt,
                attachments=list(records),
                status_message_id=status_message_id,
            )
        except Exception:
            await self.restore_pending(owner_id, chat_id, records)
            raise
        return QueuedMediaTask(task=task, attachments=records, queue_position=position)

    async def discard(self, owner_id: str, chat_id: int) -> int:
        records, _ = await self.repository.pop_pending_attachments(owner_id, chat_id)
        if not records:
            return 0
        return self.attachment_store.delete_input_records(records)

    async def purge_expired(self) -> tuple[int, int]:
        records = await self.repository.purge_expired_pending_attachments()
        if not records:
            return 0, 0
        deleted = self.attachment_store.delete_input_records(records)
        return len(records), deleted
