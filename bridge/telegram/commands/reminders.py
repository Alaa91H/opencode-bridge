"""Telegram adapters for owner-scoped reminders."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from bridge.services.reminder_service import (
    MAX_MESSAGE_LENGTH,
    Reminder,
    ReminderInputError,
    ReminderScheduler,
    ReminderStore,
    parse_reminder_args,
)

log = logging.getLogger(__name__)
Reply = Callable[[object, str], Awaitable[None]]

_STATUS_LABELS = {
    "pending": "قيد الانتظار",
    "sending": "قيد الإرسال",
    "sent": "أُرسل",
    "cancelled": "أُلغي",
    "failed": "فشل",
}


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


class ReminderCommands:
    def __init__(
        self,
        store: ReminderStore,
        scheduler: ReminderScheduler,
        reply: Reply,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.reply = reply
        self._now = now or (lambda: datetime.now(UTC))

    @staticmethod
    def _owner(update) -> tuple[str, int]:
        return str(update.effective_user.id), int(update.effective_chat.id)

    async def remind(self, update, context) -> None:
        owner_id, chat_id = self._owner(update)
        try:
            request = parse_reminder_args(context.args or [], now=self._now())
        except ReminderInputError as exc:
            await self.reply(update, f"تعذر إنشاء التذكير: {exc}")
            return
        try:
            reminder_id = await self.store.create(
                owner_id, chat_id, request.message, request.remind_at
            )
        except Exception:
            log.exception("failed to persist reminder for owner %s", owner_id)
            await self.reply(update, "تعذر حفظ التذكير. حاول مرة أخرى.")
            return
        await self.scheduler.start()
        await self.reply(
            update,
            f"تم جدولة التذكير #{reminder_id} عند {_format_time(request.remind_at)}.",
        )

    async def reminders(self, update, context) -> None:
        owner_id, _ = self._owner(update)
        try:
            reminders = await self.store.list_for_owner(owner_id)
        except Exception:
            log.exception("failed to list reminders for owner %s", owner_id)
            await self.reply(update, "تعذر قراءة التذكيرات. حاول مرة أخرى.")
            return
        if not reminders:
            await self.reply(update, "لا توجد تذكيرات قيد الانتظار.")
            return
        lines = ["التذكيرات القادمة:"]
        lines.extend(self._format_reminder(item) for item in reminders)
        await self.reply(update, "\n".join(lines)[:4_000])

    async def cancel(self, update, context) -> None:
        owner_id, _ = self._owner(update)
        args = list(context.args or [])
        if not args:
            await self.reply(update, "استخدم: /remindcancel رقم التذكير.")
            return
        try:
            reminder_id = int(args[0])
        except (TypeError, ValueError):
            await self.reply(update, "رقم التذكير يجب أن يكون عدداً صحيحاً.")
            return
        try:
            cancelled = await self.store.cancel(reminder_id, owner_id)
        except Exception:
            log.exception("failed to cancel reminder %s", reminder_id)
            await self.reply(update, "تعذر إلغاء التذكير. حاول مرة أخرى.")
            return
        if cancelled:
            await self.reply(update, f"أُلغي التذكير #{reminder_id}.")
        else:
            await self.reply(update, f"لم أجد تذكيراً قيد الانتظار بالرقم #{reminder_id}.")

    @staticmethod
    def _format_reminder(reminder: Reminder) -> str:
        status = _STATUS_LABELS.get(reminder.status, reminder.status)
        message = reminder.message
        if len(message) > MAX_MESSAGE_LENGTH:
            message = message[:MAX_MESSAGE_LENGTH] + "…"
        return f"#{reminder.id} — {_format_time(reminder.remind_at)} — {status}\n{message}"
