"""Thin Telegram adapters for task, research, and text-message use cases."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.domain.policies import RequestRejected
from bridge.services.task_service import TaskApplicationService
from bridge.telegram.rendering.tasks import render_active_tasks_and_schedules
from progress import render_persisted_activity, render_progress
from prompt_enhancer import ResearchMode

Reply = Callable[[Any, str], Awaitable[None]]
CreateStatus = Callable[[Any, int, str], Awaitable[int | None]]
EditStatus = Callable[[Any, int, int | None, str], Awaitable[None]]
WakeTasks = Callable[[], None]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]


class TaskCommands:
    def __init__(
        self,
        task_service: TaskApplicationService,
        schedule_service: Any,
        media_adapter_provider: Callable[[], Any],
        *,
        reply: Reply,
        create_status: CreateStatus,
        edit_status: EditStatus,
        wake_tasks: WakeTasks,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        live_reporters: dict[int, Any],
        progress_store: Any,
        max_message_length: int,
        research_modes: dict[str, ResearchMode],
        logger: logging.Logger | None = None,
    ) -> None:
        self.task_service = task_service
        self.schedule_service = schedule_service
        self.media_adapter_provider = media_adapter_provider
        self.reply = reply
        self.create_status = create_status
        self.edit_status = edit_status
        self.wake_tasks = wake_tasks
        self.error_message = error_message
        self.audit_write = audit_write
        self.live_reporters = live_reporters
        self.progress_store = progress_store
        self.max_message_length = max_message_length
        self.research_modes = research_modes
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

    async def abort(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._owner(update)
        try:
            result = await self.task_service.abort_running(user_id)
            cancelled = result.cancelled_task
            if cancelled:
                reporter = self.live_reporters.get(cancelled.id)
                if reporter:
                    await reporter.finalize_text(
                        "تم إلغاء الطلب.",
                        status="cancelled",
                        message="تم إرسال طلب إيقاف التنفيذ.",
                    )
                elif cancelled.status_message_id is not None:
                    await self.edit_status(
                        context.bot,
                        cancelled.chat_id,
                        cancelled.status_message_id,
                        "تم إلغاء الطلب.",
                    )
                self.audit_write(
                    "task_cancelled",
                    "cancelled",
                    actor_id=user_id,
                    details={"task_id": cancelled.id, "source": "abort"},
                )
                return
            if result.agent_stopped:
                return
            await self.reply(update.message, "لا يوجد طلب جارٍ لإيقافه.")
        except Exception as exc:
            self.log.exception("فشل إيقاف المهمة")
            await self.reply(update.message, self.error_message(exc, "إيقاف الطلب"))

    async def tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._owner(update)
        tasks = await self.task_service.active(user_id)
        jobs = await self.schedule_service.list(user_id)
        await self.reply(
            update.message,
            render_active_tasks_and_schedules(
                tasks,
                jobs,
                max_length=self.max_message_length,
            ),
        )

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._owner(update)
        try:
            cancelled = await self.task_service.cancel_current(user_id)
            if cancelled is None:
                await self.reply(update.message, "لا يوجد طلب قابل للإلغاء.")
                return

            reporter = self.live_reporters.get(cancelled.id)
            if reporter:
                await reporter.finalize_text(
                    "تم إلغاء الطلب.",
                    status="cancelled",
                    message="تم إلغاء الطلب.",
                )
            elif cancelled.status_message_id is not None:
                await self.edit_status(
                    context.bot,
                    cancelled.chat_id,
                    cancelled.status_message_id,
                    "تم إلغاء الطلب.",
                )
            self.audit_write(
                "task_cancelled",
                "cancelled",
                actor_id=user_id,
                details={"task_id": cancelled.id, "source": "cancel"},
            )
        except Exception as exc:
            self.log.exception("فشل إلغاء الطلب")
            await self.reply(update.message, self.error_message(exc, "إلغاء الطلب"))

    async def progress(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._owner(update)
        try:
            progress = self.progress_store.latest_for_owner(user_id)
            if progress and progress.owner_id == user_id:
                await self.reply(update.message, render_progress(progress, detail=True))
                return
            task = await self.task_service.latest_active(user_id)
            if task is None:
                await self.reply(update.message, "لا يوجد طلب حالي لعرض تقدمه.")
                return
            await self.reply(
                update.message,
                render_persisted_activity(task.id, task.status, task.activity, detail=True),
            )
        except Exception as exc:
            self.log.exception("فشل عرض تقدم الطلب")
            await self.reply(update.message, self.error_message(exc, "عرض تقدم الطلب"))

    async def trace(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._owner(update)
        try:
            progress = self.progress_store.latest_for_owner(user_id)
            if progress and progress.owner_id == user_id:
                await self.reply(update.message, render_progress(progress, detail=True))
                return
            task = await self.task_service.latest_active(user_id)
            if task is None:
                await self.reply(update.message, "لا يوجد سجل طلب حالي.")
                return
            await self.reply(
                update.message,
                render_persisted_activity(task.id, task.status, task.activity, detail=True),
            )
        except Exception as exc:
            self.log.exception("فشل عرض سجل الطلب")
            await self.reply(update.message, self.error_message(exc, "عرض سجل الطلب"))

    async def research(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat or not update.effective_user:
            return
        raw_command = update.message.text.split(maxsplit=1)[0].lstrip("/").split("@", 1)[0].lower()
        mode = self.research_modes.get(raw_command)
        if mode is None:
            await self.reply(update.message, "وضع البحث المطلوب غير معروف.")
            return
        prompt = " ".join(context.args).strip()
        if not prompt:
            await self.reply(update.message, f"استخدمها هيك: /{raw_command} الطلب")
            return

        try:
            self.task_service.ensure_allowed(prompt)
        except RequestRejected as exc:
            await self.reply(update.message, f"لم يُنفذ الطلب: {exc}.")
            return

        status_message_id = await self.create_status(
            context.bot,
            update.effective_chat.id,
            "جاري تجهيز البحث…",
        )
        try:
            queued = await self.task_service.enqueue_prompt(
                str(update.effective_user.id),
                update.effective_chat.id,
                prompt,
                execution_mode=mode,
                status_message_id=status_message_id,
            )
            self.wake_tasks()
            self.audit_write(
                "research_command_queued",
                "accepted",
                actor_id=str(update.effective_user.id),
                details={
                    "task_id": queued.task.id,
                    "mode": mode.value,
                    "intent": queued.enhanced.intent,
                    "research_depth": queued.enhanced.research_depth,
                    "position": queued.queue_position,
                },
            )
        except Exception as exc:
            self.log.exception("فشل تسجيل أمر البحث %s", raw_command)
            error_text = self.error_message(exc, "تسجيل مهمة البحث")
            if status_message_id is not None:
                await self.edit_status(
                    context.bot,
                    update.effective_chat.id,
                    status_message_id,
                    error_text,
                )
            else:
                await self.reply(update.message, error_text)

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text or not update.effective_chat:
            return
        text = update.message.text.strip()
        if not text:
            return

        enhanced = self.task_service.inspect_prompt(text)
        actor_id = update.effective_user.id if update.effective_user else "unknown"
        self.audit_write(
            "request_received",
            "accepted",
            actor_id=actor_id,
            details={
                "text_length": len(text),
                "is_arabic": enhanced.is_arabic,
                "is_research": enhanced.is_research,
                "intent": enhanced.intent,
                "research_depth": enhanced.research_depth,
                "requested_mode": enhanced.requested_mode,
            },
        )

        try:
            self.task_service.ensure_allowed(text)
        except RequestRejected as exc:
            self.audit_write(
                "request_blocked",
                "blocked",
                actor_id=actor_id,
                details={"policy": "request_guard", "reason": str(exc)},
            )
            await self.reply(update.message, f"لم يُنفذ الطلب: {exc}.")
            return

        media_adapter = self.media_adapter_provider()
        if await media_adapter.consume_pending_instruction(update, context, text):
            return

        user_id = self._owner(update)
        status_message_id = await self.create_status(
            context.bot,
            update.effective_chat.id,
            "جاري تجهيز الطلب…",
        )
        try:
            queued = await self.task_service.enqueue_prompt(
                user_id,
                update.effective_chat.id,
                text,
                status_message_id=status_message_id,
            )
            self.wake_tasks()
            self.audit_write(
                "task_queued",
                "accepted",
                actor_id=queued.task.owner_id,
                details={"task_id": queued.task.id, "position": queued.queue_position},
            )
        except Exception as exc:
            self.log.exception("فشل تسجيل الطلب")
            error_text = self.error_message(exc, "تسجيل الطلب")
            if status_message_id is not None:
                await self.edit_status(
                    context.bot,
                    update.effective_chat.id,
                    status_message_id,
                    error_text,
                )
            else:
                await self.reply(update.message, error_text)
