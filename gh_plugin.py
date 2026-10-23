"""GitHub administration plugin: /gh commands for repository and account work."""

from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import bot as core
import v3_plugin
from bridge.services.github_admin_service import GitHubAdminService, SubprocessGhRunner
from bridge.services.managed_repo_store import ManagedRepoStore
from bridge.telegram.commands.github import GitHubCommands

repo_store = ManagedRepoStore(core.BRIDGE_DIR / "sessions.db")
service = GitHubAdminService(
    SubprocessGhRunner(token=core.SETTINGS.github.token),
    static_repos=core.SETTINGS.workspace.allowed_repos,
    store=repo_store,
    on_allow=v3_plugin.workspace_manager.allow,
    on_disallow=v3_plugin.workspace_manager.disallow,
)
_commands_instance: GitHubCommands | None = None


def _commands() -> GitHubCommands:
    global _commands_instance
    if _commands_instance is None:
        _commands_instance = GitHubCommands(
            service,
            v3_plugin.workspace_service,
            reply=core._safe_reply,
            audit_write=core.audit.write,
            logger=core.log,
        )
    return _commands_instance


@core.authorized
async def cmd_gh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().handle(update, context)


async def install(app: Application) -> None:
    await repo_store.init()
    for slug in await repo_store.list():
        v3_plugin.workspace_manager.allow(slug)
    app.add_handler(CommandHandler("gh", cmd_gh), group=-2)
    existing = await app.bot.get_my_commands()
    command = BotCommand("gh", "إدارة مستودعات وحساب GitHub")
    await app.bot.set_my_commands(
        [command] + [item for item in existing if item.command != "gh"]
    )


async def close() -> None:
    await repo_store.close()
