"""Render schedule application data for Telegram."""

from __future__ import annotations

from typing import Any

from bridge.domain.schedules import format_interval


def scheduled_job_line(job: Any) -> str:
    state = "مفعّلة" if job.enabled else "موقوفة"
    timing = format_interval(job.repeat_seconds)
    next_run = (
        job.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
        if job.next_run_at
        else "لا يوجد موعد تالٍ"
    )
    preview = " ".join(job.prompt.split())[:100]
    return f"• {job.name} — {state} — {timing} — التالي: {next_run}\n  {preview}"


def render_schedule_list(jobs: list[Any], max_length: int) -> str:
    if not jobs:
        return "لا توجد مهام مجدولة محفوظة."
    lines = ["المهام المجدولة:"]
    for job in jobs:
        line = scheduled_job_line(job)
        candidate = "\n".join([*lines, line])
        if len(candidate) > max_length - 120:
            lines.append("… توجد مهام إضافية؛ استخدم /schedules بعد تقليل القائمة أو حذف غير المطلوب.")
            break
        lines.append(line)
    return "\n".join(lines)


def render_schedule_detail(job: Any, max_length: int) -> str:
    prompt_limit = max(300, max_length - 700)
    prompt = job.prompt
    if len(prompt) > prompt_limit:
        prompt = prompt[:prompt_limit].rstrip() + "\n… تم اختصار العرض فقط؛ الأمر الكامل محفوظ."
    next_run = job.next_run_at.strftime("%Y-%m-%d %H:%M UTC") if job.next_run_at else "لا يوجد"
    return (
        f"الاسم: {job.name}\n"
        f"الحالة: {'مفعّلة' if job.enabled else 'موقوفة'}\n"
        f"التكرار: {format_interval(job.repeat_seconds)}\n"
        f"التشغيل التالي: {next_run}\n"
        f"طول الأمر: {len(job.prompt)} محرف\n\n"
        f"{prompt}"
    )
