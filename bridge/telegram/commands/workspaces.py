"""Thin Telegram adapters for workspace selection and development tasks."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bridge.domain.policies import RequestRejected
from bridge.services.workspace_service import WorkspaceService, WorkspaceUnavailable
from bridge.telegram.rendering.workspaces import (
    render_workspace_list,
    render_workspace_selection,
    render_workspace_status,
    render_workspace_sync,
)
from workspace_manager import WorkspaceError

Reply = Callable[[Any, str], Awaitable[None]]
CreateStatus = Callable[[Any, int, str], Awaitable[int | None]]
EditStatus = Callable[[Any, int, int | None, str], Awaitable[None]]
WakeTasks = Callable[[], None]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]
AllowedCheck = Callable[[Update], bool]


class WorkspaceCommands:
    def __init__(
        self,
        service: WorkspaceService,
        *,
        reply: Reply,
        create_status: CreateStatus,
        edit_status: EditStatus,
        wake_tasks: WakeTasks,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        is_allowed: AllowedCheck,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.create_status = create_status
        self.edit_status = edit_status
        self.wake_tasks = wake_tasks
        self.error_message = error_message
        self.audit_write = audit_write
        self.is_allowed = is_allowed
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str:
        if update.effective_user is None:
            raise ValueError("تعذر تحديد المستخدم")
        return str(update.effective_user.id)

    @staticmethod
    def _chat(update: Update) -> int:
        if update.effective_chat is None:
            raise ValueError("تعذر تحديد المحادثة")
        return int(update.effective_chat.id)

    async def use(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        if not context.args:
            await self.reply(update.message, "استخدم: /use owner/repo")
            return
        try:
            status = await self.service.select(self._owner(update), context.args[0])
            await self.reply(update.message, render_workspace_selection(status))
            self.audit_write(
                "workspace_selected",
                "accepted",
                actor_id=self._owner(update),
                details={"repo": status.slug},
            )
        except WorkspaceError as exc:
            await self.reply(update.message, f"تعذر اختيار المشروع: {exc}.")

    async def repo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        try:
            status = await self.service.status(self._owner(update))
            await self.reply(update.message, render_workspace_status(status))
        except (WorkspaceError, WorkspaceUnavailable) as exc:
            await self.reply(update.message, f"تعذر قراءة المشروع: {exc}.")

    async def repos(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        items = await self.service.list(self._owner(update))
        await self.reply(update.message, render_workspace_list(items))

    async def sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        try:
            status = await self.service.sync(self._owner(update))
            await self.reply(update.message, render_workspace_sync(status))
        except (WorkspaceError, WorkspaceUnavailable) as exc:
            await self.reply(update.message, f"تعذرت المزامنة: {exc}.")

    async def _queue(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        request: str,
        *,
        source: str,
    ) -> None:
        owner_id = self._owner(update)
        chat_id = self._chat(update)
        try:
            active = await self.service.active(owner_id)
            if active is None:
                raise WorkspaceUnavailable("اختَر مشروع أولًا باستخدام /use owner/repo")
            status_message_id = await self.create_status(
                context.bot,
                chat_id,
                f"جاري تجهيز الطلب على {active.repo_slug}…",
            )
            try:
                queued = await self.service.queue(
                    owner_id,
                    chat_id,
                    request,
                    status_message_id=status_message_id,
                )
            except Exception as exc:
                error_text = self.error_message(exc, "تسجيل طلب التطوير")
                await self.edit_status(
                    context.bot,
                    chat_id,
                    status_message_id,
                    error_text,
                )
                raise
            self.wake_tasks()
            self.audit_write(
                "workspace_task_queued",
                "accepted",
                actor_id=owner_id,
                details={
                    "task_id": queued.task.id,
                    "repo": queued.repo_slug,
                    "source": source,
                },
            )
        except (WorkspaceError, WorkspaceUnavailable) as exc:
            await self.reply(update.message, str(exc))

    async def dev(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user or not update.effective_chat:
            return
        request = " ".join(context.args).strip()
        if not request:
            await self.reply(update.message, "استخدم: /dev المطلوب")
            return
        try:
            await self._queue(update, context, request, source="dev_command")
        except RequestRejected as exc:
            await self.reply(update.message, f"لم يُنفذ الطلب: {exc}.")

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (
            not update.message
            or not update.message.text
            or not update.effective_user
            or not update.effective_chat
        ):
            return
        if not self.is_allowed(update):
            return
        active = await self.service.active(self._owner(update))
        if active is None:
            return
        request = update.message.text.strip()
        if not request:
            return
        try:
            await self._queue(update, context, request, source="workspace_text")
        except RequestRejected as exc:
            await self.reply(update.message, f"لم يُنفذ الطلب: {exc}.")
        raise ApplicationHandlerStop
