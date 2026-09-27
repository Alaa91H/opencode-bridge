"""Thin Telegram adapters for AgentService."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.agent_service import AgentService, NoActiveSession
from bridge.telegram.rendering.agent import render_agent_status, render_agents, render_model_overview

Reply = Callable[[Any, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]


class AgentCommands:
    def __init__(
        self,
        service: AgentService,
        *,
        reply: Reply,
        error_message: ErrorMessage,
        startup_text: Callable[[], str],
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.error_message = error_message
        self.startup_text = startup_text
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str:
        if update.effective_user is None:
            raise ValueError("تعذر تحديد المستخدم")
        return str(update.effective_user.id)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.service.fresh_session(self._owner(update))
            await self.reply(update.message, self.startup_text())
        except Exception as exc:
            self.log.exception("فشل إنشاء جلسة البداية")
            await self.reply(update.message, self.error_message(exc, "إنشاء الجلسة"))

    async def new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await self.service.fresh_session(self._owner(update))
            await self.reply(
                update.message,
                "تم إنشاء جلسة جديدة بنجاح. أرسل طلبك للبدء.",
            )
        except Exception as exc:
            self.log.exception("فشل إنشاء جلسة جديدة")
            await self.reply(update.message, self.error_message(exc, "إنشاء جلسة جديدة"))

    async def share(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            url = await self.service.share_current(self._owner(update))
            await self.reply(
                update.message,
                f"رابط المشاركة:\n{url}" if url else "تعذر إنشاء رابط مشاركة للجلسة.",
            )
        except NoActiveSession:
            await self.reply(update.message, "لا توجد جلسة نشطة لمشاركتها.")
        except Exception as exc:
            self.log.exception("فشل إنشاء رابط مشاركة")
            await self.reply(update.message, self.error_message(exc, "إنشاء رابط المشاركة"))

    async def unshare(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            removed = await self.service.unshare_current(self._owner(update))
            await self.reply(
                update.message,
                "تم إلغاء مشاركة الجلسة."
                if removed
                else "تعذر إلغاء رابط المشاركة؛ قد لا يكون موجودًا.",
            )
        except NoActiveSession:
            await self.reply(update.message, "لا توجد جلسة نشطة لإلغاء مشاركتها.")
        except Exception as exc:
            self.log.exception("فشل إلغاء المشاركة")
            await self.reply(update.message, self.error_message(exc, "إلغاء رابط المشاركة"))

    async def model(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = self._owner(update)
        try:
            if context.args:
                await self.reply(
                    update.message,
                    "اختيار النموذج تلقائي يوميًا من نماذج OpenCode Zen المجانية، "
                    "ويُستخدم أعلى variant مدعوم للاستدلال عندما يعلنه الكتالوج. "
                    "استخدم /model لعرض الحالة الحالية.",
                )
                return
            try:
                overview = await self.service.model_overview(user_id)
            except LookupError:
                await self.reply(
                    update.message,
                    "لم يعثر OpenCode Zen على نماذج مجانية نشطة متاحة حاليًا؛ "
                    "سيبقى النموذج الحالي حتى يعود كتالوج صالح.",
                )
                return
            await self.reply(update.message, render_model_overview(overview))
        except Exception as exc:
            self.log.exception("فشل التعامل مع أمر النموذج")
            await self.reply(update.message, self.error_message(exc, "عرض أو تغيير النموذج"))

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            status = await self.service.status(self._owner(update))
            await self.reply(update.message, render_agent_status(status))
        except Exception as exc:
            self.log.exception("فشل عرض حالة الجلسة")
            await self.reply(update.message, self.error_message(exc, "عرض حالة الجلسة"))

    async def health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            health = await self.service.health()
            if health.get("healthy"):
                await self.reply(
                    update.message,
                    f"الوكيل متصل ويعمل بشكل سليم. الإصدار: {health.get('version', 'غير معروف')}",
                )
            else:
                await self.reply(
                    update.message,
                    "الوكيل استجاب لكنه لا يعلن حالة سليمة. راجع سجل الخدمة.",
                )
        except Exception as exc:
            self.log.exception("فشل الفحص الصحي")
            await self.reply(update.message, self.error_message(exc, "فحص حالة الوكيل"))

    async def agents(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            agents = await self.service.agents()
            await self.reply(update.message, render_agents(agents))
        except Exception as exc:
            self.log.exception("فشل عرض الوكلاء")
            await self.reply(update.message, self.error_message(exc, "عرض الوكلاء"))
