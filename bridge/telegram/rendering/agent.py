"""Telegram rendering for agent/session application data."""

from __future__ import annotations

from typing import Any


def render_model_overview(overview: Any) -> str:
    listed = "\n".join(f"• {name}" for name in overview.available_models)
    return (
        f"النموذج الحالي: {overview.current_model}\n"
        f"النموذج المختار تلقائيًا: {overview.preferred_model}\n"
        f"مستوى الاستدلال: {overview.variant or 'افتراضي/أقصى ما يدعمه النموذج'}\n\n"
        f"ترتيب النماذج العامة المتاحة ({len(overview.available_models)}):\n{listed}\n\n"
        "يعيد Agent Scout تقييم النماذج المجانية يوميًا ويختار الأقوى للتطوير الوكيلي، "
        "مع التحقق من أن التكلفة صفر قبل التطبيق."
    )


def render_agent_status(status: Any) -> str:
    if not status.has_session:
        return (
            f"حالة الوكيل: {'متاح' if status.healthy else 'غير متاح'}\n"
            f"الإصدار: {status.version}\n"
            "لا توجد جلسة نشطة."
        )
    return (
        "معلومات الجلسة\n"
        f"• حالة الوكيل: {'متاح' if status.healthy else 'غير متاح'}\n"
        f"• إصدار الوكيل: {status.version}\n"
        f"• الحالة: {status.state}\n"
        f"• النموذج: {status.model}\n"
        f"• مستوى الاستدلال: {status.variant or 'افتراضي'}\n"
        f"• الوكيل: {status.agent}"
    )


def render_agents(agents: list[dict]) -> str:
    if not agents:
        return "لم يُرجع الوكيل قائمة بالوكلاء المتاحين."
    lines: list[str] = []
    for agent in agents[:40]:
        name = agent.get("name") or agent.get("id") or "وكيل غير مسمى"
        mode = agent.get("mode", "غير محدد")
        description = agent.get("description") or ""
        lines.append(f"• {name} ({mode}){': ' + description if description else ''}")
    return "الوكلاء المتاحون:\n" + "\n".join(lines)
