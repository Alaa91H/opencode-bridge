"""Telegram diagnostics for host pressure and adaptive worker sizing."""

from __future__ import annotations

import os

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import bot as core
from resource_monitor import HostResourcePolicy, format_decision

_policy = HostResourcePolicy(cache_seconds=3.0)


@core.authorized
async def cmd_resources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    configured = max(1, min(int(os.environ.get("AGENT_TASK_WORKERS", "2")), 8))
    decision = _policy.decide(configured)
    await core._safe_reply(update.message, "Host resources\n\n" + format_decision(decision))


async def install(app: Application) -> None:
    app.add_handler(CommandHandler("resources", cmd_resources), group=-2)
    existing = await app.bot.get_my_commands()
    if not any(command.command == "resources" for command in existing):
        await app.bot.set_my_commands([BotCommand("resources", "Show host resource pressure")] + list(existing))
