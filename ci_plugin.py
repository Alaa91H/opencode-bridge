"""GitHub Actions compatibility plugin for the V3 Telegram runtime."""

from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import bot as core
import v3_plugin
from bridge.services.ci_service import CIStatusService
from bridge.telegram.commands.ci import CICommands
from github_ci import GitHubCIClient

CI_WORKFLOW = core.SETTINGS.github.ci_workflow
client = GitHubCIClient(core.SETTINGS.github.token)
service = CIStatusService(v3_plugin.workspace_service, client, CI_WORKFLOW)
_commands_instance: CICommands | None = None


def _commands() -> CICommands:
    global _commands_instance
    if _commands_instance is None:
        _commands_instance = CICommands(
            service,
            reply=core._safe_reply,
            audit_write=core.audit.write,
            logger=core.log,
        )
    return _commands_instance


@core.authorized
async def cmd_ci(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().ci(update, context)


async def install(app: Application) -> None:
    app.add_handler(CommandHandler("ci", cmd_ci), group=-2)
    existing = await app.bot.get_my_commands()
    command = BotCommand("ci", "Show active repository CI status")
    await app.bot.set_my_commands(
        [command] + [item for item in existing if item.command != "ci"]
    )


async def close() -> None:
    await client.close()
