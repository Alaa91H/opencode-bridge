"""Telegram adapter for the agent's skill library: see it, sync it, reload it."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.skill_sync import SkillSyncService
from bridge.telegram.rendering import skills as view
from bridge.telegram.rendering.markdown_html import html_to_plain
from bridge.telegram.rich_delivery import send_html

Reply = Callable[[Any, str], Awaitable[None]]
ReplyHtml = Callable[[Any, str, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]
ReloadService = Callable[[], Awaitable[tuple[bool, str]]]


async def _reply_html(message: Any, html: str, plain: str) -> None:
    await send_html(message, html, fallback=plain or None)


class SkillCommands:
    """``/skills`` lists what the agent can load; ``/skills sync`` refreshes it."""

    def __init__(
        self,
        service: SkillSyncService,
        *,
        reply: Reply,
        reply_html: ReplyHtml | None = None,
        error_message: ErrorMessage,
        reload_service: ReloadService | None = None,
        audit_write: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.reply_html = reply_html or _reply_html
        self.error_message = error_message
        self.reload_service = reload_service
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    async def skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        action = _argument(context)
        if action == "sync":
            await self.sync_now(update, context)
            return
        try:
            status = self.service.status()
            installed = status.get("installed", ())
            page = view.skills_page(
                source=str(status.get("source", "")),
                skills=tuple(
                    view.SkillView(name=skill.name, description=skill.description)
                    for skill in installed
                ),
                commit=str(status.get("commit", "")),
                synced_at=float(status.get("synced_at", 0.0)),
                last_status=str(status.get("last_status", "")),
                last_error=str(status.get("last_error", "")),
                needs_reload=bool(status.get("needs_reload")),
            )
            await self.reply_html(
                update.effective_message, page, html_to_plain(page),
            )
        except Exception as exc:  # a read must never look like a broken bot
            self.log.exception("فشل عرض مهارات الوكيل")
            await self.reply(
                update.effective_message,
                self.error_message(exc, "عرض مهارات الوكيل"),
            )

    async def sync_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        try:
            await self.reply(message, "🔄 عمزامن مهارات الوكيل من المستودع…")
            result = await self.service.sync()
            if result.status == "failed":
                await self.reply(
                    message,
                    self.error_message(RuntimeError(result.message), "مزامنة المهارات"),
                )
                self._audit("skills_sync", "failed", detail=result.message[:160])
                return
            await self.reload_if_needed()
            page = view.skills_updated_page(
                status=result.status,
                commit=result.commit,
                installed=result.installed,
                updated=result.updated,
                removed=result.removed,
                rejected=result.rejected,
            )
            await self.reply_html(message, page, html_to_plain(page))
            self._audit(
                "skills_sync",
                "success",
                detail=f"{len(result.installed)} new, {len(result.updated)} updated",
                commit=result.commit[:12],
            )
        except Exception as exc:
            self.log.exception("فشلت مزامنة مهارات الوكيل")
            await self.reply(message, self.error_message(exc, "مزامنة المهارات"))

    async def sync_quietly(self) -> None:
        """The scheduled path: update, then reload, never raising into a task."""
        try:
            result = await self.service.sync()
        except Exception as exc:  # the scheduler must survive a bad fetch
            self.log.info("تعذرت المزامنة التلقائية للمهارات: %s", type(exc).__name__)
            return
        if result.status != "failed":
            await self.reload_if_needed()
            self._audit(
                "skills_sync_auto",
                "success" if result.ok else "reported",
                detail=f"{len(result.installed)} new, {len(result.updated)} updated",
                commit=result.commit[:12],
            )

    async def reload_if_needed(self) -> bool:
        """Restart the agent server only when a sync actually changed skills."""
        if self.reload_service is None or not self.service.status()["needs_reload"]:
            return False
        reloaded, reason = await self.reload_service()
        if reloaded:
            self._audit("skills_reload", "success", detail="agent server restarted")
            return True
        self._audit("skills_reload", "failed", detail=reason[:160])
        self.log.info("تعذر إعادة تشغيل عميل الذكاء الاصطناعي: %s", reason)
        return False

    def _audit(self, event: str, outcome: str, *, detail: str = "", commit: str = "") -> None:
        if self.audit_write is None:
            return
        details = {"detail": detail}
        if commit:
            details["commit"] = commit
        try:
            self.audit_write(event, outcome, details=details)
        except Exception as exc:  # auditing must never break a background sync
            self.log.debug("تعذر تسجيل تدقيق المهارات: %s", type(exc).__name__)


def _argument(context: ContextTypes.DEFAULT_TYPE) -> str:
    args = getattr(context, "args", None) or []
    return str(args[0]).strip().lower() if args else ""
