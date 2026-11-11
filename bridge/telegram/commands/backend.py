"""Telegram adapter for backend selection and status."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.infrastructure.database.backend_sessions import ALLOWED_BACKENDS

Reply = Callable[[Any, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]

_BACKEND_LABELS = {
    "opencode": "OpenCode (الافتراضي)",
    "freebuff": "Freebuff CLI",
    "auto": "تلقائي (Freebuff إن كان جاهزاً، وإلا OpenCode)",
}


def _yes(value: Any) -> str:
    return "نعم" if value else "لا"


def render_backend_status(status: dict[str, Any]) -> str:
    freebuff = status.get("freebuff") if isinstance(status.get("freebuff"), dict) else {}
    lines = [
        "🧩 خلفية الوكيل",
        f"• الاختيار المحفوظ: {_BACKEND_LABELS.get(str(status.get('mode')), status.get('mode'))}",
        f"• المطبّق فعلياً: {_BACKEND_LABELS.get(str(status.get('effective')), status.get('effective'))}",
        "",
        "Freebuff CLI:",
        f"• مثبّت ومدعوم: {_yes(freebuff.get('supported'))}",
        f"• الإصدار: {freebuff.get('version') or 'غير معروف'}",
        f"• تسجيل الدخول: {_yes(freebuff.get('logged_in'))}",
    ]
    reason = str(freebuff.get("reason") or "")
    if reason and reason != "ok":
        lines.append(f"• السبب: {reason}")
    models = freebuff.get("models")
    if isinstance(models, (list, tuple)) and models:
        lines.append("• نماذج مسموحة: " + "، ".join(str(model) for model in models[:8]))
    stats = freebuff.get("stats")
    if isinstance(stats, dict) and stats:
        summary = "، ".join(f"{key}={value}" for key, value in list(stats.items())[:8])
        lines.append(f"• إحصاءات التشغيل: {summary}")
    lines.extend(
        [
            "",
            "الأوامر: /backend opencode | /backend freebuff | /backend auto",
            "التغيير يخص حسابك فقط؛ الافتراضي في الخادم يبقى OpenCode.",
        ]
    )
    return "\n".join(lines)


class BackendCommands:
    def __init__(
        self,
        router: Any,
        *,
        reply: Reply,
        error_message: ErrorMessage,
        audit_write: Callable[..., None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.router = router
        self.reply = reply
        self.error_message = error_message
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str:
        if update.effective_user is None:
            raise ValueError("تعذر تحديد المستخدم")
        return str(update.effective_user.id)

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        owner_id = self._owner(update)
        argument = (context.args[0] if context.args else "").strip().lower()
        try:
            if argument in {"", "status", "حالة"}:
                await self.reply(update.message, render_backend_status(await self.router.status(owner_id)))
                return
            if argument not in ALLOWED_BACKENDS:
                await self.reply(
                    update.message,
                    "اختر واحدة من: opencode، freebuff، auto.\n"
                    "مثال: /backend freebuff",
                )
                return
            await self.router.set_mode(owner_id, argument)
            if self.audit_write is not None:
                self.audit_write(
                    "agent_backend_selected",
                    "accepted",
                    actor_id=owner_id,
                    details={"backend": argument},
                )
            status = await self.router.status(owner_id)
            await self.reply(
                update.message,
                f"تم اختيار خلفية «{_BACKEND_LABELS[argument]}» لحسابك.\n\n"
                + render_backend_status(status),
            )
        except Exception as exc:
            self.log.exception("فشل تغيير خلفية الوكيل")
            await self.reply(update.message, self.error_message(exc, "تغيير خلفية الوكيل"))
