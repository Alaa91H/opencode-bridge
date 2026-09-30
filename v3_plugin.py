"""V3 workspace compatibility plugin.

Business rules live in bridge.services.workspace_service. This module remains
as the production compatibility/install surface for run_v3 during T02.
"""

from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import bot as core
from bridge.services.workspace_service import WorkspaceService
from bridge.telegram.commands.workspaces import WorkspaceCommands
from resource_monitor import HostResourcePolicy
from task_service_v3 import TaskServiceV3
from workspace_manager import GitWorkspaceManager
from workspace_store import WorkspaceStore

WORKSPACE_ROOT = core.SETTINGS.workspace.root
ALLOWED_REPOS = core.SETTINGS.workspace.allowed_repos
TASK_WORKERS = core.SETTINGS.agent.task_workers
ADAPTIVE_WORKERS = core.SETTINGS.features.adaptive_workers

workspace_manager = GitWorkspaceManager(WORKSPACE_ROOT, ALLOWED_REPOS)
workspace_store = WorkspaceStore(core.BRIDGE_DIR / "sessions.db")
workspace_service = WorkspaceService(
    workspace_manager,
    workspace_store,
    core._agent_service,
    core._task_application_service,
    core._request_guard,
)
_workspace_commands_instance: WorkspaceCommands | None = None


class V3TaskService(TaskServiceV3):
    def __init__(self, store, executor, poll_seconds: float = 5.0) -> None:
        self.resource_policy = HostResourcePolicy() if ADAPTIVE_WORKERS else None
        provider = None
        if self.resource_policy is not None:
            policy = self.resource_policy

            def provider() -> int:
                return policy.decide(TASK_WORKERS).allowed_workers

        super().__init__(
            store,
            executor,
            poll_seconds=poll_seconds,
            max_workers=TASK_WORKERS,
            worker_limit_provider=provider,
        )


def _wake_tasks() -> None:
    if core.task_service is None:
        raise RuntimeError("خدمة المهام غير مهيأة بعد")
    core.task_service.wake()


def _commands() -> WorkspaceCommands:
    global _workspace_commands_instance
    if _workspace_commands_instance is None:
        _workspace_commands_instance = WorkspaceCommands(
            workspace_service,
            reply=core._safe_reply,
            create_status=core._create_task_status_message,
            edit_status=core._edit_task_status_message,
            wake_tasks=_wake_tasks,
            error_message=core.user_error,
            audit_write=core.audit.write,
            is_allowed=core._is_allowed,
            logger=core.log,
        )
    return _workspace_commands_instance


async def _active(owner_id: str):
    """Compatibility wrapper retained for plugins until their T02 migration."""
    return await workspace_service.active(owner_id)


@core.authorized
async def cmd_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().use(update, context)


@core.authorized
async def cmd_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().repo(update, context)


@core.authorized
async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().repos(update, context)


@core.authorized
async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().sync(update, context)


@core.authorized
async def cmd_dev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().dev(update, context)


async def handle_workspace_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _commands().text(update, context)


async def install(app: Application) -> None:
    """Install V3 capabilities into an already initialized bridge application."""
    workspace_manager.ensure_root()
    await workspace_store.init()
    core.TaskService = V3TaskService
    core.DEFAULT_AGENT = core.SETTINGS.agent.v3_agent

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_workspace_text), group=-1)
    app.add_handler(CommandHandler("use", cmd_use), group=-1)
    app.add_handler(CommandHandler("repo", cmd_repo), group=-1)
    app.add_handler(CommandHandler("repos", cmd_repos), group=-1)
    app.add_handler(CommandHandler("sync", cmd_sync), group=-1)
    app.add_handler(CommandHandler("dev", cmd_dev), group=-1)

    current = [
        BotCommand("use", "Select a GitHub repository"),
        BotCommand("repo", "Show active repository"),
        BotCommand("repos", "List allowed repositories"),
        BotCommand("sync", "Synchronize active repository"),
        BotCommand("dev", "Run a development task"),
    ]
    existing = await app.bot.get_my_commands()
    names = {command.command for command in current}
    await app.bot.set_my_commands(
        current + [command for command in existing if command.command not in names]
    )


async def close() -> None:
    await workspace_store.close()
