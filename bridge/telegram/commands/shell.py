"""Owner-only direct terminal + full management-center text commands."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.shell_exec import format_result, policy_advisory, run_shell

Reply = Callable[[Any, str], Awaitable[None]]
ErrorMessage = Callable[[Exception, str], str]
AuditWrite = Callable[..., None]
OwnerCheck = Callable[[Any], bool]


class ShellCommands:
    """Run an arbitrary terminal command as if typed on the server."""

    def __init__(
        self,
        *,
        reply: Reply,
        error_message: ErrorMessage,
        audit_write: AuditWrite,
        is_owner: OwnerCheck,
        project_root: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.reply = reply
        self.error_message = error_message
        self.audit_write = audit_write
        self.is_owner = is_owner
        self.project_root = project_root or Path.cwd()
        self.log = logger or logging.getLogger(__name__)

    async def exec(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            await self.reply(update.message, "هذا الأمر مخصص لمالك البوت في محادثة خاصة فقط.")
            return
        command = " ".join(context.args or []).strip()
        # Also support reply-to-message form: /exec as reply runs that text.
        if not command and update.message and update.message.reply_to_message:
            command = (update.message.reply_to_message.text or "").strip()
        if not command:
            await self.reply(
                update.message,
                "الاستخدام: /exec <أمر التيرمنال>\nمثال: /exec df -h\n"
                "يُنفَّذ كما لو كتبته بنفسك داخل السيرفر (bash)، بصلاحيات مستخدم الخدمة.",
            )
            return
        self.audit_write(
            "shell_exec", "started",
            actor_id=str(update.effective_user.id) if update.effective_user else "?",
            details={"command": command[:500]},
        )
        status = await update.message.reply_text("⏳ جارٍ التنفيذ…")
        try:
            advisory = policy_advisory(command)
            result = await run_shell(command, cwd=self.project_root)
            self.audit_write(
                "shell_exec", "completed",
                actor_id=str(update.effective_user.id) if update.effective_user else "?",
                details={"command": command[:500], "code": result.code,
                         "advisory": bool(advisory)},
            )
            text = format_result(command, result)
            if advisory:
                text = advisory + "\n\n" + text
            # Telegram cap: split very long output.
            from formatter import chunk_message

            chunks = chunk_message(text, max_len=4000)
            await status.edit_text(chunks[0])
            for extra in chunks[1:4]:
                await update.message.reply_text(extra)
            if len(chunks) > 4:
                await update.message.reply_text(f"… تتمة الخرج ({len(chunks) - 4} أجزاء) تجاوزت حد العرض.")
        except Exception as exc:
            self.log.warning("فشل أمر التيرمنال المباشر: %s", type(exc).__name__)
            try:
                await status.edit_text(f"❌ {exc}")
            except Exception:
                await self.reply(update.message, self.error_message(exc, "تنفيذ أمر التيرمنال"))
