"""Telegram adapter for the owner's host-tool inventory."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.tool_manager import ToolManager, ToolManagerError
from bridge.telegram.rendering.tools import (
    size_text,
    state_text,
    tool_name,
    tool_purpose,
)

Reply = Callable[[Any, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]


class ToolCommands:
    """``/tools`` shows the catalogue; ``/tools <key>`` shows one tool."""

    def __init__(
        self,
        service: ToolManager,
        *,
        reply: Reply,
        error_message: ErrorMessage,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.error_message = error_message
        self.log = logger or logging.getLogger(__name__)

    async def tools(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        key = (context.args[0].strip().lower() if context.args else "")
        try:
            if key:
                status = await self.service.status_for(key)
                await self.reply(message, self._detail(status))
                return
            statuses = await self.service.status()
            await self.reply(message, self._overview(statuses))
        except ToolManagerError:
            await self.reply(
                message,
                "أداة غير معروفة. استخدم /tools لعرض القائمة الكاملة.",
            )
        except Exception as exc:
            self.log.exception("فشل عرض أدوات الوكيل")
            await self.reply(message, self.error_message(exc, "عرض أدوات الوكيل"))

    def _overview(self, statuses: Any) -> str:
        usable = sum(1 for status in statuses if status.usable)
        lines = [
            f"أدوات الوكيل — {usable}/{len(statuses)} جاهزة",
            "",
        ]
        for status in statuses:
            marker = "•" if status.usable else "○"
            lines.append(
                f"{marker} {tool_name(status.key)} — {state_text(status)}"
            )
            if status.missing_commands:
                lines.append(f"   ناقص: {', '.join(status.missing_commands)}")
        lines.extend([
            "",
            "للتفاصيل: /tools <المفتاح>",
            "التفعيل والتعطيل والتثبيت والحذف من لوحة التحكم ← الأدوات.",
        ])
        return "\n".join(lines)

    def _detail(self, status: Any) -> str:
        spec = status.spec
        lines = [
            f"{tool_name(spec.key)} — {state_text(status)}",
            f"الحزمة: {spec.package}",
            f"الغرض: {tool_purpose(spec.key)}",
            f"الأوامر: {', '.join(spec.commands)}",
            f"الإصدار: {status.version or '—'}",
            f"الحجم: {size_text(status.size_kib)}",
        ]
        if spec.required:
            lines.append("أداة أساسية لا يمكن تعطيلها أو حذفها.")
        if status.missing_commands:
            lines.append(f"أوامر ناقصة: {', '.join(status.missing_commands)}")
        if status.unsafe_paths:
            lines.append("تحذير: " + "; ".join(status.unsafe_paths))
        if self.service.busy:
            lines.append("عملية نظام أخرى قيد التنفيذ الآن.")
        return "\n".join(lines)
