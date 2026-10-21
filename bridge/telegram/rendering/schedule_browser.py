"""Inline schedule browser rendering."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def schedule_page(jobs, page: int = 0, page_size: int = 6):
    page_size = max(1, page_size)
    pages = max(1, (len(jobs) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = jobs[page * page_size:(page + 1) * page_size]
    rows = []
    for job in chunk:
        state = "▶️" if job.enabled else "⏸"
        rows.append([InlineKeyboardButton(f"{state} {job.name}", callback_data=f"sch:show:{job.id}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("‹ السابق", callback_data=f"sch:page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="sch:noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("التالي ›", callback_data=f"sch:page:{page+1}"))
    rows.append(nav)
    return "إدارة الجدولة", InlineKeyboardMarkup(rows)


def schedule_actions(job, page: int = 0):
    toggle = ("pause", "⏸ إيقاف") if job.enabled else ("resume", "▶️ استئناف")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle[1], callback_data=f"sch:{toggle[0]}:{job.id}:{page}"),
         InlineKeyboardButton("▶️ تشغيل الآن", callback_data=f"sch:run:{job.id}:{page}")],
        [InlineKeyboardButton("🕘 السجل", callback_data=f"sch:history:{job.id}:{page}"),
         InlineKeyboardButton("📋 تكرار", callback_data=f"sch:duplicate:{job.id}:{page}")],
        [InlineKeyboardButton("✏️ تعديل", callback_data=f"sch:edit:{job.id}:{page}"),
         InlineKeyboardButton("🗑 حذف", callback_data=f"sch:delete:{job.id}:{page}")],
        [InlineKeyboardButton("‹ القائمة", callback_data=f"sch:page:{page}")],
    ])


def delete_confirmation(job_id: int, page: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("تأكيد الحذف", callback_data=f"sch:deleteyes:{job_id}:{page}"),
        InlineKeyboardButton("إلغاء", callback_data=f"sch:show:{job_id}:{page}"),
    ]])
