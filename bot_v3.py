"""Workspace-aware V3 entrypoint built on top of the existing bridge."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

import bot as core
from task_service_v3 import TaskServiceV3
from workspace_manager import GitWorkspaceManager, WorkspaceError
from workspace_store import WorkspaceStore

log = logging.getLogger("opencode_bridge.v3")
WORKSPACE_ROOT = Path(os.environ.get("GITHUB_WORKSPACE_ROOT", "/home/ubuntu/github-workspaces"))
ALLOWED_REPOS = tuple(x.strip() for x in os.environ.get("GITHUB_ALLOWED_REPOS", "").split(",") if x.strip())
TASK_WORKERS = max(1, min(int(os.environ.get("AGENT_TASK_WORKERS", "2")), 8))
workspace_manager = GitWorkspaceManager(WORKSPACE_ROOT, ALLOWED_REPOS)
workspace_store = WorkspaceStore(core.BRIDGE_DIR / "sessions.db")
core.DEFAULT_AGENT = os.environ.get("OPENCODE_AGENT", "development-agent")


class ConfiguredTaskService(TaskServiceV3):
    def __init__(self, store, executor, poll_seconds: float = 5.0) -> None:
        super().__init__(store, executor, poll_seconds=poll_seconds, max_workers=TASK_WORKERS)


core.TaskService = ConfiguredTaskService


def workspace_prompt(repo: str, directory: str, request: str) -> str:
    return (
        "ACTIVE_WORKSPACE (trusted bridge context)\n"
        f"repository: {repo}\n"
        f"directory: {directory}\n"
        "policy: stay inside this repository; no local build or dependency installation.\n"
        "END_ACTIVE_WORKSPACE\n\n"
        f"USER_REQUEST:\n{request}"
    )


async def active_workspace(owner_id: str):
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
        await core._safe_reply(update.message, "استخدمها هيك: /use owner/repo")
        return
    try:
        status = await workspace_manager.ensure_repo(context.args[0], sync=True)
        owner_id = str(update.effective_user.id)
        await workspace_store.set(owner_id, status.slug, str(status.directory))
        await core._create_fresh_session(owner_id)
        state = "في تعديلات محلية" if status.dirty else "نظيف"
        await core._safe_reply(update.message, f"المشروع النشط: {status.slug}\nالفرع: {status.branch}\nالحالة: {state}")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذر اختيار المشروع: {exc}.")


@core.authorized
async def cmd_repo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    try:
        active = await active_workspace(str(update.effective_user.id))
        if active is None:
            await core._safe_reply(update.message, "ما في مشروع نشط. استخدم /use owner/repo.")
            return
        status = await workspace_manager.status(active.repo_slug)
        state = "في تعديلات محلية" if status.dirty else "نظيف"
        await core._safe_reply(update.message, f"{status.slug}\nالفرع: {status.branch}\nالحالة: {state}\n{status.summary}")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذر قراءة المشروع: {exc}.")


@core.authorized
async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    active = await workspace_store.get(str(update.effective_user.id))
    local = set(workspace_manager.local_repos())
    repos = workspace_manager.configured_repos()
    if not repos:
        await core._safe_reply(update.message, "اضبط GITHUB_ALLOWED_REPOS في .env أولًا.")
        return
    lines = ["المستودعات المسموحة:"]
    for slug in repos:
        labels = []
        if slug in local:
            labels.append("محلي")
        if active and active.repo_slug.casefold() == slug.casefold():
            labels.append("نشط")
        lines.append(f"• {slug}" + (f" — {', '.join(labels)}" if labels else ""))
    await core._safe_reply(update.message, "\n".join(lines))


@core.authorized
async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    try:
        active = await active_workspace(str(update.effective_user.id))
        if active is None:
            raise WorkspaceError("اختَر مشروع أولًا باستخدام /use owner/repo")
        status = await workspace_manager.sync_repo(active.repo_slug)
        note = "تم جلب آخر التغييرات فقط لأن عندك تعديلات محلية." if status.dirty else "تمت المزامنة بأمان."
        await core._safe_reply(update.message, f"{note}\n{status.slug} — {status.branch}\n{status.summary}")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذرت المزامنة: {exc}.")


@core.authorized
async def handle_message_v3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_chat or not update.effective_user:
        return
    text = update.message.text.strip()
    if not text:
        return
    reason = core.check_build(text) or core.check_hardline(text)
    if reason:
        await core._safe_reply(update.message, core.build_blocked_message(reason))
        return
    owner_id = str(update.effective_user.id)
    try:
        active = await active_workspace(owner_id)
        final_prompt = workspace_prompt(active.repo_slug, active.directory, text) if active else text
        task, position = await core.task_store.enqueue(owner_id, update.effective_chat.id, final_prompt)
        assert core.task_service is not None
        core.task_service.wake()
        project = f" على {active.repo_slug}" if active else ""
        await core._safe_reply(update.message, f"سجلت المهمة #{task.id}{project} بترتيب {position}.")
    except WorkspaceError as exc:
        await core._safe_reply(update.message, f"تعذر تجهيز مساحة العمل: {exc}.")


async def post_init_v3(app: Application) -> None:
    workspace_manager.ensure_root()
    await workspace_store.init()
    await core.post_init(app)
    commands = [
        BotCommand("use", "اختيار مشروع GitHub"),
        BotCommand("repo", "حالة المشروع النشط"),
        BotCommand("repos", "المستودعات المسموحة"),
        BotCommand("sync", "مزامنة المشروع النشط"),
        BotCommand("new", "جلسة جديدة"),
        BotCommand("abort", "إيقاف المهمة الجارية"),
        BotCommand("tasks", "عرض المهام"),
        BotCommand("progress", "تقدم المهمة"),
        BotCommand("cancel", "إلغاء مهمة"),
        BotCommand("model", "النموذج الحالي"),
        BotCommand("status", "حالة الجلسة"),
        BotCommand("health", "فحص الوكيل"),
        BotCommand("help", "المساعدة"),
    ]
    await app.bot.set_my_commands(commands)
    log.info("V3 ready with %s task workers", TASK_WORKERS)


async def post_shutdown_v3(app: Application) -> None:
    await core.post_shutdown(app)
    await workspace_store.close()


async def main() -> None:
    request = HTTPXRequest(connect_timeout=20.0, read_timeout=120.0, write_timeout=60.0, pool_timeout=60.0, http_version="1.1", proxy=core.TELEGRAM_PROXY_URL)
    updates_request = HTTPXRequest(connect_timeout=20.0, read_timeout=120.0, write_timeout=60.0, pool_timeout=60.0, http_version="1.1", proxy=core.TELEGRAM_PROXY_URL)
    app = Application.builder().token(core.TELEGRAM_BOT_TOKEN).request(request).get_updates_request(updates_request).build()

    app.add_handler(CommandHandler("use", cmd_use))
    app.add_handler(CommandHandler("repo", cmd_repo))
    app.add_handler(CommandHandler("repos", cmd_repos))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("start", core.cmd_start))
    app.add_handler(CommandHandler("new", core.cmd_new))
    app.add_handler(CommandHandler("reset", core.cmd_new))
    app.add_handler(CommandHandler("abort", core.cmd_abort))
    app.add_handler(CommandHandler("stop", core.cmd_abort))
    app.add_handler(CommandHandler("tasks", core.cmd_tasks))
    app.add_handler(CommandHandler("progress", core.cmd_progress))
    app.add_handler(CommandHandler("trace", core.cmd_trace))
    app.add_handler(CommandHandler("cancel", core.cmd_cancel))
    app.add_handler(CommandHandler("schedule", core.cmd_schedule))
    app.add_handler(CommandHandler("repeat", core.cmd_repeat))
    app.add_handler(CommandHandler("model", core.cmd_model))
    app.add_handler(CommandHandler("status", core.cmd_status))
    app.add_handler(CommandHandler("health", core.cmd_health))
    app.add_handler(CommandHandler("help", core.cmd_help))
    app.add_handler(CallbackQueryHandler(core.handle_reboot_callback, pattern=r"^reboot:(now|cancel)$"))
    app.add_handler(MessageHandler(filters.ATTACHMENT, core.handle_attachment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_v3))

    await core.store.init()
    await app.initialize()
    await post_init_v3(app)
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    await app.updater.stop()
    await app.stop()
    await post_shutdown_v3(app)
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
