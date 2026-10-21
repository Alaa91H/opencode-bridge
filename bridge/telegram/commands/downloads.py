"""Telegram adapters for managed link downloads."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.download_service import DownloadError

Reply = Callable[[Any, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]


def human_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("بايت", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "بايت" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class DownloadCommands:
    def __init__(
        self,
        service: Any,
        *,
        reply: Reply,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.error_message = error_message
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str:
        if update.effective_user is None:
            raise ValueError("المستخدم غير معروف")
        return str(update.effective_user.id)

    async def download(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Fetch one link, store it, and send the file back when it fits."""
        url = context.args[0] if context.args else ""
        if not url and update.message is not None:
            url = (update.message.text or "").strip()
        if not url or url.startswith("/"):
            await self.reply(update.message, "أرسل رابط ملف مع الأمر: /download <الرابط>")
            return
        owner_id = self._owner(update)
        self.service.ensure_directories()
        try:
            record = await self.service.fetch(owner_id, url)
        except DownloadError as exc:
            self.log.info("تعذر التنزيل: %s", exc)
            await self.reply(update.message, f"تعذّر التنزيل: {exc}")
            return
        except Exception as exc:
            self.log.exception("فشل التنزيل")
            await self.reply(update.message, self.error_message(exc, "تنزيل الملف"))
            return
        self.audit_write(
            "file_downloaded",
            "completed",
            actor_id=owner_id,
            details={
                "download_id": record.id,
                "filename": record.filename,
                "size_bytes": record.size_bytes,
                "mime": record.mime,
                "source_host": record.source_url.split("/")[2] if "//" in record.source_url else "",
            },
        )
        if not record.sendable_inline:
            await self.reply(
                update.message,
                "تم التنزيل، لكن الملف أكبر من حد الإرسال المباشر فبمسجّلته باللوحة.\n"
                "افتح «الملفات والتنزيلات» من /menu باش تشوفه وتحذّفه.",
            )
            return
        chat_id = update.effective_chat.id if update.effective_chat else None
        with Path(record.path).open("rb") as handle:
            await update.effective_bot.send_document(
                chat_id=chat_id,
                document=handle,
                filename=record.filename,
                caption=f"{record.filename} — {human_bytes(record.size_bytes)}",
            )

    async def delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await self.reply(update.message, "الاستخدام: /deletefile <معرّف الملف>")
            return
        removed = self.service.delete(self._owner(update), context.args[0])
        await self.reply(update.message, "تم الحذف." if removed else "ما في ملف بهذا المعرّف.")
