"""Thin Telegram adapter for help and maintenance commands."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

Reply = Callable[[Any, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]


class SystemCommands:
    def __init__(
        self,
        maintenance_service: Any,
        *,
        reply: Reply,
        help_text: str,
        error_message: ErrorMessage,
        logger: logging.Logger | None = None,
    ) -> None:
        self.maintenance_service = maintenance_service
        self.reply = reply
        self.help_text = help_text
        self.error_message = error_message
        self.log = logger or logging.getLogger(__name__)

    async def maintenance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            report = self.maintenance_service.latest()
            if report is None:
                await self.reply(
                    update.message,
                    "لسّا ما في تقرير صيانة يومي. أول تقرير بينحفظ بعد أول تشغيل مجدول.",
                )
                return
            if not report:
                await self.reply(
                    update.message,
                    "تقرير الصيانة الحالي فاضي. راجع سجل خدمة الصيانة.",
                )
                return
            await self.reply(update.message, report)
        except Exception as exc:
            self.log.exception("فشل عرض تقرير الصيانة")
            await self.reply(
                update.message,
                self.error_message(exc, "عرض تقرير الصيانة"),
            )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.reply(update.message, self.help_text)
