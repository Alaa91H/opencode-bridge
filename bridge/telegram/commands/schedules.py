"""Thin Telegram adapters for persistent schedule commands."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.domain.policies import RequestRejected
from bridge.domain.schedules import parse_utc_datetime, split_pipe_args
from bridge.services.schedule_service import ScheduleNotFound, ScheduleService
from bridge.telegram.rendering.schedules import render_schedule_detail, render_schedule_list

Reply = Callable[[Any, str], Awaitable[None]]
CreateStatus = Callable[[Any, int, str], Awaitable[int | None]]
EditStatus = Callable[[Any, int, int | None, str], Awaitable[None]]
WakeTasks = Callable[[], None]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]


class ScheduleCommands:
    """Translate Telegram updates to ScheduleService calls and render results."""

    def __init__(
        self,
        service: ScheduleService,
        *,
        reply: Reply,
        create_status: CreateStatus,
        edit_status: EditStatus,
        wake_tasks: WakeTasks,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        max_message_length: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.create_status = create_status
        self.edit_status = edit_status
        self.wake_tasks = wake_tasks
        self.error_message = error_message
        self.audit_write = audit_write
        self.max_message_length = max_message_length
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str:
        if update.effective_user is None:
            raise ValueError("تعذر تحديد المستخدم")
        return str(update.effective_user.id)

    @staticmethod
    def _chat_id(update: Update) -> int:
        if update.effective_chat is None:
            raise ValueError("تعذر تحديد المحادثة")
        return int(update.effective_chat.id)

    async def schedules(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        jobs = await self.service.list(self._owner(update))
        await self.reply(update.message, render_schedule_list(jobs, self.max_message_length))

    async def schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            name, when_text, prompt = split_pipe_args(" ".join(context.args), 3)
            due_at = parse_utc_datetime(when_text)
            job = await self.service.create_once(
                self._owner(update),
                self._chat_id(update),
                name,
                prompt,
                due_at,
                timezone_name="UTC",
            )
            self.wake_tasks()
            await self.reply(
                update.message,
                f"تم حفظ «{job.name}» وستعمل في {due_at.strftime('%Y-%m-%d %H:%M UTC')}.",
            )
            self.audit_write(
                "scheduled_job_created",
                "accepted",
                actor_id=job.owner_id,
                details={"schedule_job_id": job.id, "name": job.name, "repeat_seconds": None},
            )
        except RequestRejected as exc:
            await self.reply(update.message, f"تعذر إنشاء الجدولة: {exc}.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر إنشاء الجدولة: {exc}.\n"
                "الصيغة: /schedule الاسم | YYYY-MM-DD HH:MM | الأمر",
            )
        except Exception as exc:
            self.log.exception("فشل إنشاء الجدولة")
            await self.reply(update.message, self.error_message(exc, "إنشاء الجدولة"))

    async def repeat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            name, interval_text, prompt = split_pipe_args(" ".join(context.args), 3)
            job = await self.service.create_recurring(
                self._owner(update),
                self._chat_id(update),
                name,
                prompt,
                interval_text,
                timezone_name="UTC",
            )
            self.wake_tasks()
            await self.reply(update.message, f"تم حفظ «{job.name}» للتكرار {interval_text}.")
            self.audit_write(
                "scheduled_job_created",
                "accepted",
                actor_id=job.owner_id,
                details={
                    "schedule_job_id": job.id,
                    "name": job.name,
                    "repeat_seconds": job.repeat_seconds,
                },
            )
        except RequestRejected as exc:
            await self.reply(update.message, f"تعذر إنشاء الجدولة: {exc}.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر إنشاء الجدولة: {exc}.\nالصيغة: /repeat الاسم | 1d | الأمر",
            )
        except Exception as exc:
            self.log.exception("فشل إنشاء الجدولة المتكررة")
            await self.reply(update.message, self.error_message(exc, "إنشاء الجدولة المتكررة"))

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = " ".join(context.args).strip()
        if not name:
            await self.reply(update.message, "الصيغة: /schedshow الاسم")
            return
        try:
            job = await self.service.get(self._owner(update), name)
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
            return
        await self.reply(update.message, render_schedule_detail(job, self.max_message_length))

    async def rename(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            old_name, new_name = split_pipe_args(" ".join(context.args), 2)
            job = await self.service.rename(self._owner(update), old_name, new_name)
            await self.reply(update.message, f"تم تغيير الاسم إلى «{job.name}».")
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر تغيير الاسم: {exc}.\nالصيغة: /schedrename الاسم | الاسم الجديد",
            )

    async def edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            name, prompt = split_pipe_args(" ".join(context.args), 2)
            job = await self.service.replace_prompt(self._owner(update), name, prompt)
            await self.reply(update.message, f"تم تحديث أمر «{job.name}» بالكامل.")
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
        except RequestRejected as exc:
            await self.reply(update.message, f"تعذر تعديل الأمر: {exc}.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر تعديل الأمر: {exc}.\nالصيغة: /schededit الاسم | الأمر الجديد",
            )

    async def append(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            name, addition = split_pipe_args(" ".join(context.args), 2)
            job = await self.service.append_prompt(self._owner(update), name, addition)
            await self.reply(
                update.message,
                f"تم إلحاق النص بأمر «{job.name}». الطول الآن {len(job.prompt)} محرف.",
            )
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
        except RequestRejected as exc:
            await self.reply(update.message, f"تعذر إلحاق النص: {exc}.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر إلحاق النص: {exc}.\nالصيغة: /schedappend الاسم | النص الإضافي",
            )

    async def change_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            name, when_text = split_pipe_args(" ".join(context.args), 2)
            due_at = parse_utc_datetime(when_text)
            job = await self.service.change_next_run(self._owner(update), name, due_at)
            self.wake_tasks()
            await self.reply(
                update.message,
                f"تم تغيير موعد «{job.name}» إلى {due_at.strftime('%Y-%m-%d %H:%M UTC')}.",
            )
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر تغيير الموعد: {exc}.\nالصيغة: /schedtime الاسم | YYYY-MM-DD HH:MM",
            )

    async def change_interval(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            name, interval_text = split_pipe_args(" ".join(context.args), 2)
            job, label = await self.service.change_interval(self._owner(update), name, interval_text)
            self.wake_tasks()
            await self.reply(update.message, f"تم تغيير تكرار «{job.name}» إلى {label}.")
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
        except ValueError as exc:
            await self.reply(
                update.message,
                f"تعذر تغيير التكرار: {exc}.\nالصيغة: /schedinterval الاسم | 1d أو once",
            )

    async def pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = " ".join(context.args).strip()
        if not name:
            await self.reply(update.message, "الصيغة: /schedpause الاسم")
            return
        try:
            job = await self.service.pause(self._owner(update), name)
            await self.reply(update.message, f"تم إيقاف «{job.name}».")
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")

    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = " ".join(context.args).strip()
        if not name:
            await self.reply(update.message, "الصيغة: /schedresume الاسم")
            return
        try:
            job = await self.service.resume(self._owner(update), name)
            self.wake_tasks()
            await self.reply(update.message, f"تم تشغيل «{job.name}».")
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")

    async def delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = " ".join(context.args).strip()
        if not name:
            await self.reply(update.message, "الصيغة: /scheddelete الاسم")
            return
        try:
            await self.service.delete(self._owner(update), name)
            await self.reply(update.message, "تم حذف المهمة المجدولة.")
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")

    async def run_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        name = " ".join(context.args).strip()
        if not name:
            await self.reply(update.message, "الصيغة: /schedrun الاسم")
            return
        owner_id = self._owner(update)
        try:
            job = await self.service.get(owner_id, name)
        except ScheduleNotFound:
            await self.reply(update.message, "لم أجد مهمة مجدولة بهذا الاسم.")
            return

        chat_id = self._chat_id(update)
        status_message_id = await self.create_status(
            context.bot,
            chat_id,
            f"جاري تشغيل «{job.name}»…",
        )
        try:
            job, task = await self.service.run_now(
                owner_id,
                job.name,
                status_message_id=status_message_id,
            )
        except ScheduleNotFound:
            await self.edit_status(
                context.bot,
                chat_id,
                status_message_id,
                "تعذر تشغيل المهمة المجدولة.",
            )
            return
        self.wake_tasks()
        self.audit_write(
            "scheduled_job_run_now",
            "accepted",
            actor_id=owner_id,
            details={"schedule_job_id": job.id, "task_id": task.id, "name": job.name},
        )
