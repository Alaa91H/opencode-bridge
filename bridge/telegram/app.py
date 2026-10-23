"""Telegram application composition helpers.

The legacy bot module passes its compatibility callbacks here during T02.
"""

from __future__ import annotations

from typing import Any

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters


def core_commands() -> list[BotCommand]:
    commands = [        BotCommand("start", "🚀 البدء"),
        BotCommand("new", "➕ جديد"),
        BotCommand("reset", "🔄 إعادة ضبط"),
        BotCommand("abort", "⏹ إيقاف"),
        BotCommand("stop", "⏹ إيقاف"),
        BotCommand("tasklist", "📋 المهام — متكامل"),
        BotCommand("tasks", "🔘 الأوامر الحالية"),
        BotCommand("scheduled", "📅 المجدولة"),
        BotCommand("schedule", "⏰ جدولة"),
        BotCommand("schedules", "📅 قائمة مجدولة"),
        BotCommand("schedshow", "👁 مهمة"),
        BotCommand("schedhistory", "🕘 سجل"),
        BotCommand("schedrun", "▶ تشغيل"),
        BotCommand("scheddelete", "🗑 حذف"),
        BotCommand("progress", "📊 تقدم"),
        BotCommand("trace", "📝 سجل"),
        BotCommand("cancel", "🚫 إلغاء"),
        BotCommand("retry", "🔄 إعادة"),
        BotCommand("failed", "❌ فاشلة"),
        BotCommand("discard", "🗑 حذف مرفقات"),
        BotCommand("model", "🤖 نموذج"),
        BotCommand("skills", "🛠 مهارات"),
        BotCommand("status", "ℹ️ حالة"),
        BotCommand("health", "❤️ صحة"),
        BotCommand("agents", "👥 وكلاء"),
        BotCommand("config", "⚙️ إعدادات"),
        BotCommand("settings", "🔧 تخصيص"),
        BotCommand("limits", "📏 حدود"),
        BotCommand("metrics", "📈 قياسات"),
        BotCommand("search", "🔍 بحث"),
        BotCommand("deepresearch", "🧠 عميق"),
        BotCommand("news", "📰 أخبار"),
        BotCommand("compare", "📊 مقارنة"),
        BotCommand("factcheck", "✅ تدقيق"),
        BotCommand("verify", "✔️ تحقق"),
        BotCommand("open", "🔗 فتح"),
        BotCommand("extract", "📥 استخراج"),
        BotCommand("share", "📤 مشاركة"),
        BotCommand("unshare", "❌ إلغاء مشاركة"),
        BotCommand("download", "📥 تحميل"),
        BotCommand("deletefile", "🗑 حذف"),
        BotCommand("mirror", "🪞 مرآة"),
        BotCommand("remind", "⏰ تذكير"),
        BotCommand("reminders", "📌 تذكيرات"),
        BotCommand("ghrepos", "🐙 مستودعات"),
        BotCommand("ghcreate", "🐙 إنشاء"),
        BotCommand("ghclone", "🐙 استنساخ"),
        BotCommand("ghstatus", "🐙 حالة"),
        BotCommand("ghpr", "🐙 PR"),
        BotCommand("ghissue", "🐙 قضية"),
        BotCommand("ghrelease", "🐙 إصدار"),
        BotCommand("ghbranch", "🐙 فرع"),
        BotCommand("ghsecret", "🐙 سر"),
        BotCommand("ghhelp", "🐙 مساعدة"),
        BotCommand("help", "❓ مساعدة"),
        BotCommand("menu", "📋 لوحة"),
        BotCommand("keyboard", "⌨️ لوحة"),
        BotCommand("hidekeyboard", "🚫 إخفاء"),
        BotCommand("prompt", "📝 برومبت"),
        BotCommand("topics", "📰 مواضيع"),
        BotCommand("taskshow", "👁 مهمة"),
        BotCommand("dailies", "📆 يومية"),    ]
    # The registry grew by appending blocks over time, so several entries were
    # listed twice. Telegram renders duplicates in the command menu and the
    # payload is sent on every startup; keep the first description only.
    unique: list[BotCommand] = []
    seen: set[str] = set()
    for command in commands:
        if command.command in seen:
            continue
        seen.add(command.command)
        unique.append(command)
    return unique


def register_core_handlers(
    app: Application,
    handlers: Any,
    research_command_names: tuple[str, ...],
) -> None:
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("new", handlers.cmd_new))
    app.add_handler(CommandHandler("reset", handlers.cmd_new))
    app.add_handler(CommandHandler("abort", handlers.cmd_abort))
    app.add_handler(CommandHandler("stop", handlers.cmd_abort))
    app.add_handler(CommandHandler("tasks", handlers.cmd_tasks))
    app.add_handler(CommandHandler("tasklist", handlers.cmd_tasks))
    app.add_handler(CommandHandler("scheduled", handlers.cmd_schedules))
    # Two professional buttons for combined task list: current + scheduled
    app.add_handler(CommandHandler("tasklist", handlers.cmd_tasks))
    app.add_handler(CommandHandler("direct", handlers.cmd_direct))
    app.add_handler(CommandHandler("daily", handlers.cmd_daily))
    app.add_handler(CommandHandler("failed", handlers.cmd_failed))
    app.add_handler(CommandHandler("retry", handlers.cmd_retry))
    app.add_handler(CommandHandler("progress", handlers.cmd_progress))
    app.add_handler(CommandHandler("trace", handlers.cmd_trace))
    app.add_handler(CommandHandler("cancel", handlers.cmd_cancel))
    app.add_handler(CommandHandler("discard", handlers.cmd_discard))
    app.add_handler(CommandHandler("schedule", handlers.cmd_schedule))
    app.add_handler(CommandHandler("repeat", handlers.cmd_repeat))
    app.add_handler(CommandHandler("schedules", handlers.cmd_schedules))
    app.add_handler(CommandHandler("schedshow", handlers.cmd_schedshow))
    app.add_handler(CommandHandler("schedhistory", handlers.cmd_schedhistory))
    app.add_handler(CommandHandler("draft", handlers.cmd_draft))
    app.add_handler(CommandHandler("schedrename", handlers.cmd_schedrename))
    app.add_handler(CommandHandler("schededit", handlers.cmd_schededit))
    app.add_handler(CommandHandler("schedappend", handlers.cmd_schedappend))
    app.add_handler(CommandHandler("schedtime", handlers.cmd_schedtime))
    app.add_handler(CommandHandler("schedinterval", handlers.cmd_schedinterval))
    app.add_handler(CommandHandler("schedtimezone", handlers.cmd_schedtimezone))
    app.add_handler(CommandHandler("schedrun", handlers.cmd_schedrun))
    app.add_handler(CommandHandler("schedpause", handlers.cmd_schedpause))
    app.add_handler(CommandHandler("schedresume", handlers.cmd_schedresume))
    app.add_handler(CommandHandler("scheddelete", handlers.cmd_scheddelete))
    app.add_handler(CallbackQueryHandler(handlers.handle_schedule_callback, pattern=r"^sch:"))
    app.add_handler(CallbackQueryHandler(handlers.handle_model_callback, pattern=r"^mdl:"))
    app.add_handler(CallbackQueryHandler(handlers.handle_panel_callback, pattern=r"^pnl:"))
    # The agent's own questions and permission requests: the only callbacks that
    # carry a decision back to OpenCode.
    if getattr(handlers, "handle_agent_interaction", None) is not None:
        app.add_handler(
            CallbackQueryHandler(
                handlers.handle_agent_interaction,
                pattern=r"^ag:",
            )
        )
    app.add_handler(CallbackQueryHandler(handlers.handle_control_callback, pattern=r"^ux:"))
    app.add_handler(CommandHandler("menu", handlers.cmd_menu))
    app.add_handler(CommandHandler("keyboard", handlers.cmd_keyboard))
    app.add_handler(CommandHandler("hidekeyboard", handlers.cmd_hide_keyboard))
    app.add_handler(CommandHandler("download", handlers.cmd_download))
    app.add_handler(CommandHandler("deletefile", handlers.cmd_deletefile))
    app.add_handler(CommandHandler("mirror", handlers.cmd_mirror))
    app.add_handler(CommandHandler("prompt", handlers.cmd_prompt))
    app.add_handler(CommandHandler("topics", handlers.cmd_topics))
    app.add_handler(CommandHandler("taskshow", handlers.cmd_taskshow))
    app.add_handler(CommandHandler("dailies", handlers.cmd_dailies))
    app.add_handler(CommandHandler("exec", handlers.cmd_exec))
    app.add_handler(CommandHandler("sh", handlers.cmd_exec))
    app.add_handler(CommandHandler("shell", handlers.cmd_exec))
    app.add_handler(CommandHandler("term", handlers.cmd_exec))
    app.add_handler(CommandHandler("share", handlers.cmd_share))
    app.add_handler(CommandHandler("unshare", handlers.cmd_unshare))
    app.add_handler(CommandHandler("model", handlers.cmd_model))
    if getattr(handlers, "cmd_skills", None) is not None:
        app.add_handler(CommandHandler("skills", handlers.cmd_skills))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    app.add_handler(CommandHandler("health", handlers.cmd_health))
    app.add_handler(CommandHandler("agents", handlers.cmd_agents))
    app.add_handler(CommandHandler("maintenance", handlers.cmd_maintenance))
    app.add_handler(CommandHandler("config", handlers.cmd_config))
    # settings disabled due to module-order; /config provides same customization
    app.add_handler(CommandHandler("limits", handlers.cmd_limits))
    app.add_handler(CommandHandler("metrics", handlers.cmd_metrics))
    app.add_handler(CommandHandler(research_command_names, handlers.cmd_research_mode))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    # GitHub project-management commands: implemented by ``GitHubCommandHandler`` and
    # exposed through the ``cmd_gh*`` shims on the bot module so they stay aligned
    # with ``core_commands()``.
    app.add_handler(CommandHandler("ghrepos", handlers.cmd_ghrepos))
    app.add_handler(CommandHandler("ghcreate", handlers.cmd_ghcreate))
    app.add_handler(CommandHandler("ghclone", handlers.cmd_ghclone))
    app.add_handler(CommandHandler("ghstatus", handlers.cmd_ghstatus))
    app.add_handler(CommandHandler("ghpr", handlers.cmd_ghpr))
    app.add_handler(CommandHandler("ghissue", handlers.cmd_ghissue))
    app.add_handler(CommandHandler("ghrelease", handlers.cmd_ghrelease))
    app.add_handler(CommandHandler("ghbranch", handlers.cmd_ghbranch))
    app.add_handler(CommandHandler("ghsecret", handlers.cmd_ghsecret))
    app.add_handler(CommandHandler("ghhelp", handlers.cmd_ghhelp))
    # Reminder / notification commands (sequential + scheduled)
    # Reminder commands registered only if available in module
    if getattr(handlers, "cmd_remind", None) is not None:
        app.add_handler(CommandHandler("remind", handlers.cmd_remind))
    if getattr(handlers, "cmd_reminders", None) is not None:
        app.add_handler(CommandHandler("reminders", handlers.cmd_reminders))
    app.add_handler(
        CallbackQueryHandler(
            handlers.handle_reboot_callback,
            pattern=r"^reboot:(now|cancel)$",
        )
    )
    app.add_handler(MessageHandler(filters.ATTACHMENT, handlers.handle_attachment))
    # A reply to an agent question card is the answer, not a new prompt. This
    # handler must stay above the general text handler: in the default group only
    # the first matching handler runs, and a question the agent asked must never
    # be answered by starting a second task.
    if getattr(handlers, "handle_agent_interaction_reply", None) is not None:
        app.add_handler(
            MessageHandler(
                filters.REPLY & filters.TEXT & ~filters.COMMAND,
                handlers.handle_agent_interaction_reply,
            )
        )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message)
    )


def build_application(
    *,
    token: str,
    request: Any,
    updates_request: Any,
    post_init: Any,
    post_shutdown: Any,
    base_url: str | None = None,
    base_file_url: str | None = None,
    local_mode: bool = False,
) -> Application:
    builder = Application.builder().token(token)
    if base_url:
        builder = builder.base_url(base_url)
    if base_file_url:
        builder = builder.base_file_url(base_file_url)
    if local_mode:
        builder = builder.local_mode(True)
    return (
        builder
        .request(request)
        .get_updates_request(updates_request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
