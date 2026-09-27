"""Telegram adapter for attachment intake and pending-file instructions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from attachments import AttachmentError
from bridge.domain.policies import RequestRejected
from bridge.services.media_service import MediaTaskService, PendingAttachmentsExpired

Reply = Callable[[Any, str], Awaitable[None]]
CreateStatus = Callable[[Any, int, str], Awaitable[int | None]]
EditStatus = Callable[[Any, int, int | None, str], Awaitable[None]]
WakeTasks = Callable[[], None]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]


class TelegramMediaAdapter:
    """Keep Telegram-specific buffering/download mechanics out of application services."""

    def __init__(
        self,
        attachment_store: Any,
        media_service: MediaTaskService,
        *,
        reply: Reply,
        create_status: CreateStatus,
        edit_status: EditStatus,
        wake_tasks: WakeTasks,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        debounce_seconds: float,
        logger: logging.Logger | None = None,
    ) -> None:
        self.attachment_store = attachment_store
        self.media_service = media_service
        self.reply = reply
        self.create_status = create_status
        self.edit_status = edit_status
        self.wake_tasks = wake_tasks
        self.error_message = error_message
        self.audit_write = audit_write
        self.debounce_seconds = max(0.25, min(float(debounce_seconds), 5.0))
        self.log = logger or logging.getLogger(__name__)
        self.media_group_batches: dict[tuple[int, str], dict[str, object]] = {}
        self.media_group_flush_tasks: dict[tuple[int, str], asyncio.Task[None]] = {}

    async def queue_batch(
        self,
        owner_id: str,
        chat_id: int,
        prompt: str,
        attachments: list[Any],
        bot: Any,
    ) -> None:
        records = self.media_service.records_from_attachments(attachments)
        status_message_id = await self.create_status(bot, chat_id, "جاري تجهيز الملفات…")
        try:
            queued = await self.media_service.queue_records(
                owner_id,
                chat_id,
                prompt,
                records,
                status_message_id=status_message_id,
                merge_pending=True,
            )
        except RequestRejected as exc:
            await self.edit_status(
                bot,
                chat_id,
                status_message_id,
                f"لم يُنفذ الطلب: {exc}.",
            )
            return
        except Exception as exc:
            error_text = self.error_message(exc, "تسجيل الطلب")
            await self.edit_status(bot, chat_id, status_message_id, error_text)
            raise

        self.wake_tasks()
        self.audit_write(
            "attachment_task_queued",
            "accepted",
            actor_id=owner_id,
            details={
                "task_id": queued.task.id,
                "position": queued.queue_position,
                "attachment_count": len(queued.attachments),
                "total_bytes": sum(int(item.get("size", 0) or 0) for item in queued.attachments),
            },
        )

    async def stage_batch(
        self,
        owner_id: str,
        chat_id: int,
        attachments: list[Any],
    ) -> None:
        records = self.media_service.records_from_attachments(attachments)
        staged = await self.media_service.stage_records(owner_id, chat_id, records)
        self.audit_write(
            "attachment_staged",
            "accepted",
            actor_id=owner_id,
            details={
                "attachment_count": len(staged.attachments),
                "expires_at": staged.batch.expires_at.isoformat(),
            },
        )

    async def ingest_batch(
        self,
        owner_id: str,
        chat_id: int,
        attachments: list[Any],
        prompt: str,
        bot: Any,
    ) -> None:
        prompt = prompt.strip()
        if prompt:
            await self.queue_batch(owner_id, chat_id, prompt, attachments, bot)
        else:
            await self.stage_batch(owner_id, chat_id, attachments)

    async def _flush_media_group_after_delay(self, key: tuple[int, str], bot: Any) -> None:
        current_task = asyncio.current_task()
        batch: dict[str, object] | None = None
        try:
            await asyncio.sleep(self.debounce_seconds)
            batch = self.media_group_batches.pop(key, None)
            if not batch:
                return
            attachments = list(batch.get("attachments") or [])
            await self.ingest_batch(
                str(batch["owner_id"]),
                int(batch["chat_id"]),
                attachments,
                str(batch.get("prompt") or ""),
                bot,
            )
        except asyncio.CancelledError:
            return
        except (AttachmentError, ValueError) as exc:
            if batch:
                self.attachment_store.delete_input_records(
                    [item.to_record() for item in list(batch.get("attachments") or [])]
                )
            await bot.send_message(chat_id=key[0], text=f"ما قدرت أجهّز مجموعة المرفقات: {exc}.")
        except Exception as exc:
            if batch:
                self.attachment_store.delete_input_records(
                    [item.to_record() for item in list(batch.get("attachments") or [])]
                )
            self.log.exception("فشل تجهيز مجموعة مرفقات تيليغرام")
            await bot.send_message(
                chat_id=key[0],
                text=self.error_message(exc, "تجهيز مجموعة المرفقات"),
            )
        finally:
            if self.media_group_flush_tasks.get(key) is current_task:
                self.media_group_flush_tasks.pop(key, None)

    async def handle_attachment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        attachment = None
        try:
            owner_id = str(update.effective_user.id)
            chat_id = update.effective_chat.id
            attachment = await self.attachment_store.download_from_message(
                update.message,
                context.bot,
                owner_id,
            )
            prompt = (update.message.caption or "").strip()
            media_group_id = getattr(update.message, "media_group_id", None)

            if media_group_id:
                key = (chat_id, str(media_group_id))
                batch = self.media_group_batches.setdefault(
                    key,
                    {"owner_id": owner_id, "chat_id": chat_id, "attachments": [], "prompt": ""},
                )
                if str(batch["owner_id"]) != owner_id:
                    raise AttachmentError("مجموعة الوسائط لا تطابق صاحب المهمة")
                cast_attachments = batch["attachments"]
                if not isinstance(cast_attachments, list):
                    raise AttachmentError("حالة مجموعة الوسائط غير صالحة")
                cast_attachments.append(attachment)
                if prompt and not batch.get("prompt"):
                    batch["prompt"] = prompt
                previous = self.media_group_flush_tasks.get(key)
                if previous:
                    previous.cancel()
                self.media_group_flush_tasks[key] = asyncio.create_task(
                    self._flush_media_group_after_delay(key, context.bot)
                )
                return

            await self.ingest_batch(owner_id, chat_id, [attachment], prompt, context.bot)
        except (AttachmentError, ValueError) as exc:
            if attachment is not None:
                self.attachment_store.delete_input_records([attachment.to_record()])
            await self.reply(update.message, f"ما قدرت أستلم المرفق: {exc}.")
        except Exception as exc:
            if attachment is not None:
                self.attachment_store.delete_input_records([attachment.to_record()])
            self.log.exception("فشل استلام مرفق تيليغرام")
            await self.reply(update.message, self.error_message(exc, "استلام المرفق"))

    async def consume_pending_instruction(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
    ) -> bool:
        if not update.effective_user or not update.effective_chat:
            return False
        owner_id = str(update.effective_user.id)
        chat_id = update.effective_chat.id
        try:
            records = await self.media_service.take_pending(owner_id, chat_id)
        except PendingAttachmentsExpired:
            await self.reply(
                update.message,
                "انتهت مهلة الملفات المعلّقة. أعد إرسالها ثم أرسل الأمر.",
            )
            return True

        if not records:
            return False

        status_message_id = await self.create_status(
            context.bot,
            chat_id,
            "جاري تجهيز الملفات…",
        )
        try:
            queued = await self.media_service.queue_taken_pending(
                owner_id,
                chat_id,
                text,
                records,
                status_message_id=status_message_id,
            )
        except RequestRejected as exc:
            await self.edit_status(
                context.bot,
                chat_id,
                status_message_id,
                f"لم يُنفذ الطلب: {exc}.",
            )
            return True
        except Exception as exc:
            self.log.exception("فشل ربط الأمر بالملفات المعلّقة")
            error_text = self.error_message(exc, "تسجيل الطلب")
            await self.edit_status(
                context.bot,
                chat_id,
                status_message_id,
                error_text,
            )
            return True

        self.wake_tasks()
        self.audit_write(
            "attachment_task_queued",
            "accepted",
            actor_id=owner_id,
            details={
                "task_id": queued.task.id,
                "position": queued.queue_position,
                "attachment_count": len(queued.attachments),
                "total_bytes": sum(int(item.get("size", 0) or 0) for item in queued.attachments),
                "source": "pending_instruction",
            },
        )
        return True

    async def discard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.effective_chat:
            return
        deleted = await self.media_service.discard(
            str(update.effective_user.id),
            update.effective_chat.id,
        )
        if not deleted:
            await self.reply(update.message, "ما في مرفقات معلّقة لإلغائها.")
            return
        await self.reply(update.message, "تم إلغاء الملفات المعلّقة.")

    async def cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(
                    min(60, max(15, self.media_service.pending_ttl_seconds // 4))
                )
                records, deleted = await self.media_service.purge_expired()
                if records:
                    self.audit_write(
                        "pending_attachments_expired",
                        "cleaned",
                        details={"records": records, "files_deleted": deleted},
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.log.warning(
                    "تعذر تنظيف المرفقات المعلّقة المنتهية: %s",
                    type(exc).__name__,
                )

    async def shutdown(self) -> None:
        for task in list(self.media_group_flush_tasks.values()):
            task.cancel()
        self.media_group_flush_tasks.clear()
        orphaned_records = [
            attachment.to_record()
            for batch in self.media_group_batches.values()
            for attachment in list(batch.get("attachments") or [])
            if hasattr(attachment, "to_record")
        ]
        self.media_group_batches.clear()
        if orphaned_records:
            self.attachment_store.delete_input_records(orphaned_records)
