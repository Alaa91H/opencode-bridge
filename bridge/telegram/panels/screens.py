"""Control-panel screens.

Every value rendered here comes from a real provider supplied by ``bot.py``.
Nothing in this module invents state, and no preference toggle is offered that
the runtime does not actually read.
"""

from __future__ import annotations

from typing import Any

from bridge.telegram.panels.registry import (
    ACT,
    MENU,
    NAVIGATE,
    Panel,
    PanelAction,
    PanelView,
    RAW,
)

BACK_LABEL = "‹ رجوع"


def _row(*actions: PanelAction) -> tuple[PanelAction, ...]:
    return tuple(actions)


def _nav(label: str, target: str) -> PanelAction:
    return PanelAction(label=label, verb=NAVIGATE, arg=target)


# ---------------------------------------------------------------- main menu


async def menu_screen(context: Any, data: dict[str, Any]) -> PanelView:
    del data
    text = (
        "🏠 <b>لوحة التحكم</b>\n\n"
        "اختر القسم اللي بدك إياه. كل قسم بيعرض حالته الحقيقية "
        "وبيتيحلك تتحكم فيه من نفس المكان."
    )
    return PanelView(
        text=text,
        rows=(
            _row(_nav("⚙️ الإعدادات", "settings")),
            _row(_nav("🤖 النموذج ومستوى الاستدلال", "model")),
            _row(_nav("📋 المهام", "tasks")),
            _row(_nav("🗓 الجدولة", "schedules")),
            _row(_nav("💾 الملفات والتنزيلات", "downloads")),
            _row(_nav("🖥 حالة النظام", "system")),
            _row(_nav("🆘 المساعدة والأوامر", "help")),
        ),
    )


# -------------------------------------------------------------------- system

_STATE_LABELS = {
    "healthy": "🟢healthy — سليم",
    "degraded": "🟡 degraded — متأثر",
    "unhealthy": "🔴 unhealthy — معطّل",
}


async def system_screen(context: Any, data: dict[str, Any]) -> PanelView:
    health = data["health"]
    limits = data.get("limits") or {}
    state = getattr(getattr(health, "state", None), "value", None) or str(getattr(health, "state", "?"))
    lines = ["🖥 <b>حالة النظام</b>", f"الإجمالي: {_STATE_LABELS.get(state, state)}"]
    for component in getattr(health, "components", ()) or ():
        value = getattr(getattr(component, "state", None), "value", None) or str(component.state)
        detail = getattr(component, "detail", "") or ""
        suffix = f" — {detail}" if detail else ""
        lines.append(f"• {component.name}: {_STATE_LABELS.get(value, value)}{suffix}")
    if limits:
        lines.append("")
        lines.append("📏 <b>الحدود الفعلية</b>")
        for key, value in sorted(limits.items()):
            lines.append(f"• {key}: {value}")
    return PanelView(
        text="\n".join(lines),
        rows=(_row(PanelAction(label="🔄 تحديث", verb="refresh")),),
    )


# ----------------------------------------------------------------- settings


async def settings_screen(context: Any, data: dict[str, Any]) -> PanelView:
    config = data.get("config") or {}
    preferences = data.get("preferences")
    model = getattr(preferences, "model_preference", None) or "تلقائي"
    variant = getattr(preferences, "model_variant", None) or "تلقائي"
    pinned = bool(getattr(preferences, "model_pinned", False))
    level = getattr(preferences, "notification_level", "normal")
    style = getattr(preferences, "output_style", "summary")
    retention = getattr(preferences, "retention_days", 30)
    lines = [
        "⚙️ <b>الإعدادات</b>",
        "",
        f"🤖 النموذج: {model}",
        f"🎚 مستوى الاستدلال: {variant}",
        f"📌 التثبيت اليدوي: {'مفعّل' if pinned else 'معطّل'}",
    ]
    rows: list[tuple[PanelAction, ...]] = [
        _row(_nav("🤖 تغيير النموذج والمستوى", "model")),
        _row(
            _option("🔔 التقدّم", "notification"),
            _option("📄 أسلوب العرض", "output"),
        ),
        _row(
            _option("🗑 مدة الاحتفاظ", "retention"),
        ),
    ]
    lines += ["", "🔧 <b>الإعدادات الفعلية (غير سرّية)</b>"]
    if config:
        for key, value in sorted(config.items()):
            lines.append(f"• {key}: {value}")
    else:
        lines.append("• لا توجد إعدادات معروضة")
    return PanelView(text="\n".join(lines), rows=tuple(rows) + (_row(_nav("📏 عرض الحدود", "system")),))


NOTIFICATION_CHOICES = (
    ("silent", "🔇 صامت"),
    ("errors", "⚠️ أخطاء فقط"),
    ("normal", "🔔 عادي"),
    ("verbose", "🔊 مفصّل"),
)
OUTPUT_CHOICES = (
    ("summary", "📝 ملخّص"),
    ("full", "📚 تفصيلي"),
    ("compact", "⚡ مختصر"),
)
RETENTION_CHOICES = (("1", "يوم"), ("7", "٧ أيام"), ("30", "٣٠ يوم"), ("90", "٩٠ يوم"))


def _option(label: str, name: str) -> PanelAction:
    """A button that opens the choice sub-screen for one preference field."""
    return PanelAction(label=label, verb=NAVIGATE, arg=f"settings.{name}")


# --------------------------------------------------------------------- tasks

_STATUS_LABELS = {
    "queued": "🕒 في الانتظار",
    "running": "▶️ قيد التنفيذ",
    "failed": "❌ فاشلة",
    "completed": "✅ مكتملة",
    "cancelled": "🚫 ملغاة",
}


async def tasks_screen(context: Any, data: dict[str, Any]) -> PanelView:
    active = data.get("active") or []
    failed = data.get("failed") or []
    lines = ["📋 <b>المهام</b>", ""]
    rows: list[tuple[PanelAction, ...]] = []
    if active:
        lines.append(f"<b>قيد العمل ({len(active)})</b>")
        for task in active:
            lines.append(
                f"• {_STATUS_LABELS.get(task.status, task.status)} — {str(task.prompt)[:60]}"
            )
        latest = active[0]
        rows.append(
            _row(
                PanelAction(
                    label="🛑 إلغاء الجارية",
                    verb="cancel",
                    arg=str(latest.id),
                    confirm="هل تريد إلغاء المهمة الجارية؟",
                    destructive=True,
                )
            )
        )
    else:
        lines.append("لا توجد مهام قيد العمل.")
    if failed:
        lines += ["", f"<b>فاشلة ({len(failed)})</b>"]
        for task in failed[:5]:
            error = str(task.last_error or task.status)
            lines.append(f"• {str(task.prompt)[:50]} — {error[:30]}")
        for task in failed[:3]:
            rows.append(
                _row(
                    PanelAction(
                        label=f"🔁 إعادة محاولة: {str(task.prompt)[:24]}",
                        verb="retry",
                        arg=str(task.id),
                    )
                )
            )
    else:
        lines += ["", "لا توجد مهام فاشلة."]
    return PanelView(
        text="\n".join(lines),
        rows=tuple(rows) + (_row(PanelAction(label="🔄 تحديث", verb="refresh")),),
    )


# ----------------------------------------------------------------- schedules


async def schedules_screen(context: Any, data: dict[str, Any]) -> PanelView:
    jobs = data.get("schedules") or []
    lines = ["🗓 <b>المهام المجدولة</b>", ""]
    rows: list[tuple[PanelAction, ...]] = []
    if jobs:
        for job in jobs[:12]:
            mark = "▶️" if job.enabled else "⏸"
            lines.append(f"{mark} {job.name}")
        for job in jobs[:6]:
            rows.append(
                _row(
                    PanelAction(
                        label=f"{'⏸ إيقاف' if job.enabled else '▶️ تفعيل'}: {job.name}",
                        verb="toggle_schedule",
                        arg=str(job.id),
                    ),
                    PanelAction(
                        label=f"🗑 حذف: {job.name}",
                        verb="delete_schedule",
                        arg=str(job.id),
                        confirm=f"هل تريد حذف الجدولة «{job.name}» نهائيًا؟",
                        destructive=True,
                    ),
                )
            )
    else:
        lines.append("لا توجد مهام مجدولة. أضف واحدة بأمر /schedule <اسم> <المهمة>.")
    return PanelView(
        text="\n".join(lines),
        rows=tuple(rows) + (_row(PanelAction(label="🔄 تحديث", verb="refresh")),),
    )


# ----------------------------------------------------------------- downloads


async def downloads_screen(context: Any, data: dict[str, Any]) -> PanelView:
    del data
    return PanelView(
        text=(
            "💾 <b>الملفات والتنزيلات</b>\n\n"
            "هذه الشاشة قيد الإنشاء. تنزيل الملفات المباشرة والفيديو "
            "سيضاف كإصدار مستقل بعد استكمال باقي اللوحة."
        ),
        rows=(),
    )


# ----------------------------------------------------------------- model link


async def model_screen(context: Any, data: dict[str, Any]) -> PanelView:
    del data
    return PanelView(
        text=(
            "🤖 <b>النموذج ومستوى الاستدلال</b>\n\n"
            "اضغط «اختيار النموذج» لفتح قائمة الموديلات، وبعدها "
            "تختار مستوى الاستدلال من الأزرار."
        ),
        rows=(_row(PanelAction(label="⚡ اختيار النموذج", verb=RAW, arg="mdl:open")),),
    )


# ---------------------------------------------------------------------- help


async def help_screen(context: Any, data: dict[str, Any]) -> PanelView:
    commands = data.get("commands") or ()
    lines = ["🆘 <b>الأوامر المتاحة</b>", ""]
    lines.append("• <b>عام</b>: /help /menu")
    lines.append(f"• <b>الجلسة</b>: {_group(commands, _SESSION_COMMANDS)}")
    lines.append(f"• <b>المهام</b>: {_group(commands, _TASK_COMMANDS)}")
    lines.append(f"• <b>الجدولة</b>: {_group(commands, _SCHEDULE_COMMANDS)}")
    known = _SESSION_COMMANDS | _TASK_COMMANDS | _SCHEDULE_COMMANDS
    rest = [
        item
        for item in commands
        if item not in known and not item.startswith("sched")
    ]
    lines.append(f"• <b>أخرى</b>: {_group(commands, tuple(rest))}")
    return PanelView(text="\n".join(lines), rows=())


def _group(commands: Any, members: tuple[str, ...]) -> str:
    wanted = set(members)
    return " ".join(f"/{item}" for item in commands if item in wanted) or "—"


_SESSION_COMMANDS = {"start", "new", "reset", "abort", "stop", "model", "status", "share", "unshare"}
_TASK_COMMANDS = {"tasks", "failed", "retry", "progress", "trace", "cancel", "discard"}
_SCHEDULE_COMMANDS = {"schedule", "repeat", "schedules", "schedshow", "schedrun"}

# Preference fields the runtime genuinely consumes. Each entry drives one
# choice sub-screen: title, the (value, arabic label) pairs, how to read the
# current value, and the UserPreferences field an action writes.
_OPTION_FIELDS = {
    "notification": (
        "🔔 مستوى إشعارات التقدّم",
        NOTIFICATION_CHOICES,
        lambda preferences: preferences.notification_level,
        "notification_level",
    ),
    "output": (
        "📄 أسلوب عرض التقدّم",
        OUTPUT_CHOICES,
        lambda preferences: preferences.output_style,
        "output_style",
    ),
    "retention": (
        "🗑 مدة الاحتفاظ بالمرفقات",
        RETENTION_CHOICES,
        lambda preferences: str(preferences.retention_days),
        "retention_days",
    ),
}


def build_panels() -> dict[str, Panel]:
    panels = {
        MENU: Panel(name=MENU, title="القائمة الرئيسية", render=menu_screen, needs=()),
        "system": Panel(name="system", title="حالة النظام", render=system_screen, needs=("health", "limits")),
        "settings": Panel(
            name="settings",
            title="الإعدادات",
            render=settings_screen,
            needs=("config", "preferences"),
        ),
        "tasks": Panel(name="tasks", title="المهام", render=tasks_screen, needs=("active", "failed")),
        "schedules": Panel(
            name="schedules", title="الجدولة", render=schedules_screen, needs=("schedules",)
        ),
        "downloads": Panel(name="downloads", title="الملفات", render=downloads_screen, needs=()),
        "model": Panel(name="model", title="النموذج", render=model_screen, needs=()),
        "help": Panel(name="help", title="المساعدة", render=help_screen, needs=("commands",)),
    }
    for name, (title, choices, current_of, apply_field) in _OPTION_FIELDS.items():
        panels[f"settings.{name}"] = Panel(
            name=f"settings.{name}",
            title=title,
            render=_option_renderer(current_of, apply_field, choices),
            needs=("preferences",),
        )
    return panels


def _option_renderer(
    current_of: Any, apply_field: str, choices: tuple[tuple[str, str], ...]
):
    """Build the renderer for one preference-choice screen."""

    async def render(context: Any, data: dict[str, Any]) -> PanelView:
        del context
        current = current_of(data["preferences"])
        rows = [
            _row(
                PanelAction(
                    label=f"{'✅ ' if value == current else ''}{label}",
                    verb=ACT,
                    arg=f"{apply_field}={value}",
                )
            )
            for value, label in choices
        ]
        return PanelView(text="اختر القيمة التي تناسبك:", rows=tuple(rows))

    return render
