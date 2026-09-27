"""Telegram-specific access-control middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

F = TypeVar("F", bound=Callable[..., Awaitable[None]])
Reply = Callable[[Any, str], Awaitable[None]]
AuditWrite = Callable[..., None]


class TelegramAccessController:
    def __init__(
        self,
        allowed_users: set[int],
        allowed_chat_ids: set[int],
        *,
        reply: Reply,
        unauthorized_text: Callable[[], str],
        audit_write: AuditWrite,
        logger: logging.Logger | None = None,
    ) -> None:
        self.allowed_users = allowed_users
        self.allowed_chat_ids = allowed_chat_ids
        self.reply = reply
        self.unauthorized_text = unauthorized_text
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)

    def is_allowed(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or user.is_bot or user.id not in self.allowed_users:
            return False
        if chat is None:
            return False
        return chat.type == ChatType.PRIVATE or chat.id in self.allowed_chat_ids

    def wrap(self, handler: F) -> F:
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not self.is_allowed(update):
                if update.message:
                    user_id = update.effective_user.id if update.effective_user else "مجهول"
                    self.log.warning("محاولة وصول غير مصرح بها من المستخدم %s", user_id)
                    self.audit_write(
                        "access_attempt",
                        "denied",
                        actor_id=user_id,
                        details={"handler": handler.__name__},
                    )
                    await self.reply(update.message, self.unauthorized_text())
                return
            actor_id = update.effective_user.id if update.effective_user else "مجهول"
            self.log.info("المستخدم %s طلب %s", actor_id, handler.__name__)
            self.audit_write(
                "handler_invoked",
                "accepted",
                actor_id=actor_id,
                details={"handler": handler.__name__},
            )
            await handler(update, context)

        return wrapper  # type: ignore[return-value]
