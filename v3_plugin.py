"""V3 workspace plugin for the existing Telegram bridge.

The plugin keeps the mature bot runtime intact and layers repository selection,
workspace context injection, and bounded queue concurrency on top.
"""

from __future__ import annotations

import os
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, ContextTypes, MessageHandler, filters

import bot as core
from task_service_v3 import TaskServiceV3
from workspace_manager import GitWorkspaceManager, WorkspaceError
from workspace_store import WorkspaceStore

WORKSPACE_ROOT = Path(os.environ.get("GITHUB_WORKSPACE_ROOT", "/home/ubuntu/github-workspaces"))
ALLOWED_REPOS = tuple(value.strip() for value in os.environ.get("GITHUB_ALLOWED_REPOS", "").split(",") if value.strip())
TASK_WORKERS = max(1, min(int(os.environ.get("AGENT_TASK_WORKERS", "2")), 8))

workspace_manager = GitWorkspaceManager(WORKSPACE_ROOT, ALLOWED_REPOS)
workspace_store = WorkspaceStore(core.BRIDGE_DIR / "sessions.db")


class V3TaskService(TaskServiceV3):
    def __init__(self, store, executor, poll_seconds: float = 5.0) -> None:
        super().__init__(store, executor, poll_seconds=poll_seconds, max_workers=TASK_WORKERS)


def workspace_prompt(repo_slug: str, directory: str, request: str) -> str:
    return (
        "ACTIVE_WORKSPACE (trusted bridge context)\n"
        f"repository: {repo_slug}\n"
        f"directory: {directory}\n"
        "policy: work only inside this repository; do not build or install dependencies locally.\n"
        "END_ACTIVE_WORKSPACE\n\n"
        f"USER_REQUEST:\n{request}"
    )


async def _active(owner_id: str):
    active = await workspace_store.get(owner_id)
    if active is None:
        return None
    slug = workspace_manager.require_allowed(active.repo_slug)
    expected = workspace_manager.repo_path(slug)
    if expected != Path(active.directory).resolve():
        raise WorkspaceError("مسار مساحة العمل المحفوظ لم يعد صالحًا")
    return active


@core.authorized
async def cmd_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not context.args:
        await core._safe_reply(update.message, "استخدم: /use owner/repo")
        return
    try:
        status = await workspace_manager.ensure_repo(context.args[0], sync=True)
        owner_id = str(update.effective_user.id)
        await workspace_store.set(owner_id, status.slug, str(status.directory))
        await core._create_fresh_session(owner_id)
        state = "dirty" if status.dirty else "clean"
        await core._safe_reply(
            update.message,
            f"Active repository: {status.slug}\nBranch: {status.branch}\nState: {state}",
        )
        core.audit.write("workspace_selected", "accepted", actor_id=owner_id, details={"repo": status.slug})
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذر اختيار المشروع: {exc}.")


@core.authorized
async def cmd_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    try:
        active = await _active(str(update.effective_user.id))
        if active is None:
            await core._safe_reply(update.message, "ما في مشروع نشط. استخدم /use owner/repo.")
            return
        status = await workspace_manager.status(active.repo_slug)
        state = "dirty" if status.dirty else "clean"
        await core._safe_reply(update.message, f"{status.slug}\nBranch: {status.branch}\nState: {state}\n{status.summary}")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذر قراءة المشروع: {exc}.")


@core.authorized
async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    active = await workspace_store.get(str(update.effective_user.id))
    local = set(workspace_manager.local_repos())
    configured = workspace_manager.configured_repos()
    if not configured:
        await core._safe_reply(update.message, "اضبط GITHUB_ALLOWED_REPOS في .env أولًا.")
        return
    lines = ["Allowed repositories:"]
    for slug in configured:
        labels = []
        if slug in local:
            labels.append("local")
        if active and active.repo_slug.casefold() == slug.casefold():
            labels.append("active")
        lines.append(f"• {slug}" + (f" — {', '.join(labels)}" if labels else ""))
    await core._safe_reply(update.message, "\n".join(lines))


@core.authorized
async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    try:
        active = await _active(str(update.effective_user.id))
        if active is None:
            raise WorkspaceError("اختَر مشروع أولًا باستخدام /use owner/repo")
        status = await workspace_manager.sync_repo(active.repo_slug)
        note = "Fetched remote state; local edits were preserved." if status.dirty else "Repository synchronized safely."
        await core._safe_reply(update.message, f"{note}\n{status.slug} — {status.branch}\n{status.summary}")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذرت المزامنة: {exc}.")


@core.authorized
async def cmd_dev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    request = " ".join(context.args).strip()
    if not request:
        await core._safe_reply(update.message, "استخدم: /dev المطلوب")
        return
    reason = core.check_build(request) or core.check_hardline(request)
    if reason:
        await core._safe_reply(update.message, core.build_blocked_message(reason))
        return
    try:
        owner_id = str(update.effective_user.id)
        active = await _active(owner_id)
        if active is None:
            raise WorkspaceError("اختَر مشروع أولًا باستخدام /use owner/repo")
        prompt = workspace_prompt(active.repo_slug, active.directory, request)
        task, position = await core.task_store.enqueue(owner_id, update.effective_chat.id, prompt)
        assert core.task_service is not None
        core.task_service.wake()
        await core._safe_reply(update.message, f"Queued development task #{task.id} on {active.repo_slug} at position {position}.")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, str(exc))


async def handle_workspace_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user or not update.effective_chat:
        return
    if not core._is_allowed(update):
        return
    active = await _active(str(update.effective_user.id))
    if active is None:
        return
    text = update.message.text.strip()
    if not text:
        return
    reason = core.check_build(text) or core.check_hardline(text)
    if reason:
        await core._safe_reply(update.message, core.build_blocked_message(reason))
        raise ApplicationHandlerStop
    prompt = workspace_prompt(active.repo_slug, active.directory, text)
    task, position = await core.task_store.enqueue(str(update.effective_user.id), update.effective_chat.id, prompt)
    assert core.task_service is not None
    core.task_service.wake()
    await core._safe_reply(update.message, f"Queued task #{task.id} on {active.repo_slug} at position {position}.")
    raise ApplicationHandlerStop


async def install(app: Application) -> None:
    """Install V3 capabilities into an already initialized bridge application."""
    workspace_manager.ensure_root()
    await workspace_store.init()
    core.TaskService = V3TaskService
    core.DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "development-agent")

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
    await app.bot.set_my_commands(current + [command for command in existing if command.command not in names])


async def close() -> None:
    await workspace_store.close()
