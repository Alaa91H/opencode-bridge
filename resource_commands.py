"""Resource diagnostics compatibility plugin for the Telegram runtime."""

from __future__ import annotations

import os

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import bot as core
from bridge.services.resource_service import ResourceStatusService
from bridge.telegram.commands.resources import ResourceCommands
from bridge.telegram.rendering.resources import format_controller as _format_controller
from resource_monitor import HostResourcePolicy

_policy = HostResourcePolicy(cache_seconds=3.0)
_commands_instance: ResourceCommands | None = None


def _configured_workers() -> int:
    try:
        value = int(os.environ.get("AGENT_TASK_WORKERS", "2"))
    except ValueError:
        value = 2
    return max(1, min(value, 8))


def _controller_status():
    service = getattr(core, "task_service", None)
    limiter = getattr(service, "worker_limit", None)
    status = getattr(limiter, "status", None)
    if not callable(status):
        return None
    try:
        return status()
    except Exception:
        return None


def _shadow_readiness():
    service = getattr(core, "task_service", None)
    policy = getattr(service, "resource_policy", None)
    readiness = getattr(policy, "readiness", None)
    if not callable(readiness):
        return None
    try:
        return readiness()
    except Exception:
        return None


service = ResourceStatusService(
    _policy,
    configured_workers=_configured_workers,
    controller_status=_controller_status,
    shadow_readiness=_shadow_readiness,
)


def _commands() -> ResourceCommands:
    global _commands_instance
    if _commands_instance is None:
        _commands_instance = ResourceCommands(service, reply=core._safe_reply)
    return _commands_instance


@core.authorized
async def cmd_resources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().resources(update, context)


async def install(app: Application) -> None:
    app.add_handler(CommandHandler("resources", cmd_resources), group=-2)
    existing = await app.bot.get_my_commands()
    if not any(command.command == "resources" for command in existing):
        await app.bot.set_my_commands(
            [BotCommand("resources", "Show host resource pressure")] + list(existing)
        )
