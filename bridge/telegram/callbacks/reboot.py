"""Telegram reboot-decision callback adapter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes


class RebootCallbackAdapter:
    def __init__(
        self,
        *,
        is_allowed: Callable[[Update], bool],
        request_path: Path,
        decision_path: Path,
        read_state: Callable[[Path], dict[str, Any] | None],
        write_state: Callable[[Path, dict[str, Any]], None],
        audit_write: Callable[..., None],
    ) -> None:
        self.is_allowed = is_allowed
        self.request_path = request_path
        self.decision_path = decision_path
        self.read_state = read_state
        self.write_state = write_state
        self.audit_write = audit_write

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data not in {"reboot:now", "reboot:cancel"}:
            return
        if not self.is_allowed(update):
            await query.answer("غير مصرح لك باستخدام هذا الزر.", show_alert=True)
            return
        request = self.read_state(self.request_path)
        if request is None or request.get("status") != "awaiting":
            await query.answer("لا يوجد طلب إعادة تشغيل معلّق.", show_alert=True)
            return

        action = "reboot_now" if query.data == "reboot:now" else "cancel"
        self.write_state(
            self.decision_path,
            {
                "action": action,
                "request_id": str(request.get("request_id") or ""),
                "source": "telegram_button",
            },
        )
        outcome = (
            "تم تسجيل طلب إعادة التشغيل."
            if action == "reboot_now"
            else "تم إلغاء طلب إعادة التشغيل."
        )
        await query.answer(outcome, show_alert=True)
        actor_id = update.effective_user.id if update.effective_user else "unknown"
        self.audit_write(
            "reboot_decision",
            action,
            actor_id=actor_id,
            details={"source": "telegram_button"},
        )
