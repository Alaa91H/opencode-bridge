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
    visible_tasks = [task for task in tasks if task.status in {"running", "queued"}]
    if visible_tasks:
        lines.append("الطلبات الحالية:")
        for task in visible_tasks[:10]:
            state = "قيد التنفيذ" if task.status == "running" else "بانتظار التنفيذ"
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
