"""Telegram rendering for task/application views."""

from __future__ import annotations

from typing import Any

from bridge.telegram.rendering.schedules import scheduled_job_line


def render_active_tasks_and_schedules(
    tasks: list[Any],
    jobs: list[Any],
    *,
    max_length: int,
) -> str:
    lines: list[str] = []
    visible_tasks = [task for task in tasks if task.status in {"running", "queued", "leased", "retrying"}]
    if visible_tasks:
        lines.append("الطلبات الحالية:")
        for task in visible_tasks[:10]:
            state = {"running": "قيد التنفيذ", "leased": "جاري التجهيز",
                     "retrying": "محفوظة — بانتظار الاستئناف التلقائي"}.get(task.status, "بانتظار التنفيذ")
            preview = " ".join(task.prompt.split())[:100]
            lines.append(f"• {state} — {preview}")

    if jobs:
        if lines:
            lines.append("")
        lines.append("المهام المجدولة:")
        for job in jobs:
            line = scheduled_job_line(job)
            candidate = "\n".join([*lines, line])
            if len(candidate) > max_length - 120:
                lines.append("… توجد مهام مجدولة إضافية.")
                break
            lines.append(line)

    return "\n".join(lines) if lines else "لا توجد طلبات حالية أو مهام مجدولة."


def render_queue(running: Any | None, waiting: list[Any], *, max_length: int) -> str:
    """The owner-facing work queue: what runs now and what waits in order."""
    if running is None and not waiting:
        return (
            "طابور التنفيذ فاضي.\n"
            "أي أمر ترسله بينفّذ مباشرة، وأي أمر يوصل أثناء مهمة جارية بينتظر دوره تلقائيًا."
        )

    def preview(task: Any, limit: int = 90) -> str:
        return " ".join(str(task.prompt).split())[:limit]

    lines: list[str] = []
    if running is not None:
        lines.append("قيد التنفيذ الآن:")
        lines.append(f"• #{running.id} — {preview(running)}")
        if waiting:
            lines.append("")

    if waiting:
        lines.append(f"بانتظار التنفيذ ({len(waiting)}):")
        for position, task in enumerate(waiting, 1):
            flags = []
            if task.status == "retrying":
                flags.append("إعادة محاولة")
            if getattr(task, "priority", 0):
                flags.append(f"أولوية {task.priority:+d}")
            suffix = f" · {'، '.join(flags)}" if flags else ""
            lines.append(f"{position}. #{task.id} — {preview(task)}{suffix}")
        lines.append("")
        lines.append(
            "الإدارة: /queue remove <id> · /queue up <id> · /queue down <id> · /queue clear"
        )
    elif running is not None:
        lines.append("ما في أوامر بانتظار التنفيذ.")

    text = "\n".join(lines)
    if len(text) > max_length:
        text = text[: max(1, max_length - 40)].rstrip() + "\n… القائمة أطول من العرض."
    return text
