"""Telegram rendering for the agent's skill library."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class SkillView:
    name: str
    description: str


def _ago(seconds: float) -> str:
    if seconds <= 0:
        return "لم تتم بعد"
    delta = datetime.now(UTC).timestamp() - seconds
    if delta < 90:
        return "قبل دقيقة"
    if delta < 5400:
        return f"قبل {int(delta // 60)} دقيقة"
    if delta < 172800:
        return f"قبل {int(delta // 3600)} ساعة"
    return f"قبل {int(delta // 86400)} يوم"


def skills_page(
    *,
    source: str,
    skills: tuple[SkillView, ...],
    commit: str,
    synced_at: float,
    last_status: str,
    last_error: str,
    needs_reload: bool,
) -> str:
    """One page an owner reads to know what the agent can do and how fresh it is."""
    lines = [f"<b>🧠 مهارات الوكيل ({len(skills)})</b>"]
    short_commit = commit[:8] if commit else "—"
    lines.append(f"<b>المصدر:</b> <code>{_escape(source)}</code>")
    lines.append(f"<b>آخر مزامنة:</b> {_ago(synced_at)} · <code>{_escape(short_commit)}</code>")
    if needs_reload:
        lines.append(
            "<b>⏳ في انتظار إعادة تشغيل عميل الذكاء الاصطناعي ليرصد المهارات الجديدة.</b>"
        )
    if last_status == "empty":
        lines.append("<b>ℹ️ المستودع لا يحتوي مهارات بعد.</b> أضِف مجلدات <code>&lt;name&gt;/SKILL.md</code> وسيصل أثرها تلقائياً.")
    if last_error:
        lines.append(f"<b>⚠️ آخر خطأ:</b> {_escape(last_error[:160])}")
    if skills:
        lines.append("")
        for skill in skills:
            lines.append(f"• <b>{_escape(skill.name)}</b> — {_escape(skill.description[:220])}")
    else:
        lines.append("")
        lines.append("لا توجد مهارات مثبتة حالياً.")
    return "\n".join(lines)


def skills_updated_page(
    *, status: str, commit: str, installed: tuple[str, ...],
    updated: tuple[str, ...], removed: tuple[str, ...], rejected: tuple[str, ...],
) -> str:
    """The report after an update, in plain words about what changed."""
    titles = {
        "synced": "✅ تمت المزامنة",
        "unchanged": "✅ لا جديد",
        "empty": "ℹ️ المستودع فارغ",
        "failed": "⚠️ فشلت المزامنة",
    }
    lines = [f"<b>{titles.get(status, status)}</b>"]
    if commit:
        lines.append(f"<b>التعريف:</b> <code>{_escape(commit[:12])}</code>")
    if installed:
        lines.append(f"<b>جديدة:</b> {_escape('، '.join(installed))}")
    if updated:
        lines.append(f"<b>مُحدَّثة:</b> {_escape('، '.join(updated))}")
    if removed:
        lines.append(f"<b>محذوفة:</b> {_escape('، '.join(removed))}")
    if rejected:
        lines.append("")
        lines.append("<b>مرفوضة (لم تُثبَّت):</b>")
        for reason in rejected[:8]:
            lines.append(f"• {_escape(reason[:160])}")
    lines.append("")
    lines.append("العميل سيقرأها عند إعادة تشغيله؛ أكّدتُ ذلك تلقائياً إن كانت هناك مهارة جديدة.")
    return "\n".join(lines)
