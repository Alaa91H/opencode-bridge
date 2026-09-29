"""Telegram application composition helpers.

The legacy bot module passes its compatibility callbacks here during T02.
"""

from __future__ import annotations

from typing import Any

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters


def core_commands() -> list[BotCommand]:
    return [
        BotCommand("start", "بدء جلسة جديدة"),
        BotCommand("new", "إنشاء جلسة جديدة"),
        BotCommand("reset", "إعادة ضبط الجلسة"),
        BotCommand("abort", "إيقاف الطلب الجاري"),
        BotCommand("stop", "إيقاف الطلب الجاري"),
        BotCommand("tasks", "عرض الطلبات الحالية"),
        BotCommand("failed", "عرض المهام الفاشلة"),
        BotCommand("retry", "إعادة محاولة مهمة فاشلة"),
        BotCommand("progress", "عرض تقدم الطلب الحالي"),
        BotCommand("trace", "عرض سجل الطلب الحالي"),
        BotCommand("cancel", "إلغاء الطلب الحالي"),
        BotCommand("discard", "حذف المرفقات المعلّقة"),
        BotCommand("schedule", "إنشاء مهمة مجدولة باسم"),
        BotCommand("repeat", "إنشاء مهمة متكررة باسم"),
        BotCommand("schedules", "عرض المهام المجدولة"),
        BotCommand("schedshow", "عرض تفاصيل مهمة مجدولة"),
        BotCommand("schedhistory", "عرض سجل تشغيل الجدولة"),
        BotCommand("draft", "إنشاء وإدارة Draft دائم"),
        BotCommand("schedrename", "تغيير اسم مهمة مجدولة"),
        BotCommand("schededit", "استبدال أمر مهمة مجدولة"),
        BotCommand("schedappend", "إلحاق نص بأمر مجدول"),
        BotCommand("schedtime", "تغيير وقت التشغيل التالي"),
        BotCommand("schedinterval", "تغيير فترة التكرار"),
        BotCommand("schedtimezone", "تغيير المنطقة الزمنية"),
        BotCommand("schedrun", "تشغيل مهمة مجدولة الآن"),
        BotCommand("schedpause", "إيقاف مهمة مجدولة"),
        BotCommand("schedresume", "تشغيل مهمة مجدولة"),
        BotCommand("scheddelete", "حذف مهمة مجدولة"),
        BotCommand("model", "اختيار النموذج ومستوى الاستدلال"),
        BotCommand("status", "عرض حالة الجلسة"),
        BotCommand("health", "فحص اتصال الوكيل"),
        BotCommand("agents", "عرض الوكلاء المتاحين"),
        BotCommand("maintenance", "عرض آخر تقرير صيانة"),
        BotCommand("config", "عرض الإعدادات الفعلية غير السرية"),
        BotCommand("limits", "عرض الحدود الفعلية الحالية"),
        BotCommand("search", "بحث موثّق سريع"),
        BotCommand("deepresearch", "بحث عميق متعدد المصادر"),
        BotCommand("extreme", "بحث شديد العمق"),
        BotCommand("news", "بحث إخباري حديث"),
        BotCommand("compare", "مقارنة موثّقة"),
        BotCommand("factcheck", "تدقيق ادعاء"),
        BotCommand("verify", "تحقق من معلومة"),
        BotCommand("open", "فحص رابط أو مصدر"),
        BotCommand("extract", "استخراج بيانات"),
        BotCommand("share", "إنشاء رابط مشاركة"),
        BotCommand("unshare", "إلغاء رابط المشاركة"),
        BotCommand("help", "المساعدة"),
    ]


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
    app.add_handler(CommandHandler("share", handlers.cmd_share))
    app.add_handler(CommandHandler("unshare", handlers.cmd_unshare))
    app.add_handler(CommandHandler("model", handlers.cmd_model))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    app.add_handler(CommandHandler("health", handlers.cmd_health))
    app.add_handler(CommandHandler("agents", handlers.cmd_agents))
    app.add_handler(CommandHandler("maintenance", handlers.cmd_maintenance))
    app.add_handler(CommandHandler("config", handlers.cmd_config))
    app.add_handler(CommandHandler("limits", handlers.cmd_limits))
    app.add_handler(CommandHandler(research_command_names, handlers.cmd_research_mode))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(
        CallbackQueryHandler(
            handlers.handle_reboot_callback,
            pattern=r"^reboot:(now|cancel)$",
        )
    )
    app.add_handler(MessageHandler(filters.ATTACHMENT, handlers.handle_attachment))
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
