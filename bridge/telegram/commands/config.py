"""Thin Telegram commands for effective configuration visibility."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.telegram.rendering.config import render_config, render_limits

Reply = Callable[[Any, str], Awaitable[None]]


class ConfigCommands:
    def __init__(self, service: Any, *, reply: Reply) -> None:
        self.service = service
        self.reply = reply

    @staticmethod
    def _owner(update: Update) -> str | None:
        return str(update.effective_user.id) if update.effective_user else None

    async def config(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = self.service.public_config(self._owner(update))
        await self.reply(update.message, render_config(payload))

    async def limits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = self.service.effective_limits(self._owner(update))
        await self.reply(update.message, render_limits(payload))
