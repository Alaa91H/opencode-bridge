"""Professional combined task list display under two buttons: current + scheduled."""
from __future__ import annotations

from typing import Any


def format_task_list(
    sequential: list[Any],
    active: list[Any],
    scheduled: list[Any],
    counts: dict[str, int],
) -> str:
    lines = ["📋 **قائمة المهام — عرض متكامل**\n"]

    # Button-style section 1: Sequential commands + current active tasks
    lines.append("🔘 **الأوامر والمهام الحالية:**")
    if sequential:
        lines.append(f"  • الطابور المتسلسل: {len(sequential)} أمر")
        for cmd in sequential[:5]:
            status_icon = "▶" if getattr(cmd, "status", "") == "running" else "⏳"
            lines.append(f"    {status_icon} #{cmd.id} {cmd.prompt[:60]}" + (" (يعمل)" if getattr(cmd, "status", "") == "running" else ""))
    else:
        lines.append("  • لا توجد أوامر متسلسلة حالياً.")

    if active:
        lines.append(f"  • المهام النشطة: {len(active)} مهمة")
        for task in active[:5]:
            lines.append(f"    • #{task.id} {task.prompt[:50]} — حالة: {task.status}")
    else:
        lines.append("  • لا توجد مهام نشطة حالياً.")

    # Button-style section 2: Scheduled tasks (daily / specific date)
    lines.append("\n🔘 **المهام المجدولة:**")
    if scheduled:
        lines.append(f"  • المجدولة: {len(scheduled)} مهمة")
        for job in scheduled[:5]:
            due = getattr(job, "next_run_at", None)
            due_text = f" عند {due:%Y-%m-%d %H:%M}" if due else ""
            lines.append(f"    • {job.name} ({job.prompt[:40]}){due_text}")
    else:
        lines.append("  • لا توجد مهام مجدولة حالياً.")

    # Summary stats
    lines.append("\n📊 **إحصاءات:**")
    for k, v in counts.items():
        if v:
            lines.append(f"  • {k}: {v}")

    lines.append("\n---\n📌 تم ترتيب المهام تحت زرين: الأوامر الحالية | المهام المجدولة")
    return "\n".join(lines)
