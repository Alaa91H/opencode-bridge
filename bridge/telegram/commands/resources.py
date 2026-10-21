"""Thin Telegram adapter for resource diagnostics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.telegram.rendering.resources import render_resource_snapshot

Reply = Callable[[Any, str], Awaitable[None]]


class ResourceCommands:
    def __init__(self, service: Any, *, reply: Reply) -> None:
        self.service = service
        self.reply = reply

    async def resources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await self.reply(update.message, render_resource_snapshot(self.service.snapshot()))
