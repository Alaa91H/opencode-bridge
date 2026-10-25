"""Telegram rendering for the owner-facing operational metrics snapshot."""

from __future__ import annotations

from typing import Any

_GIB = 1024 ** 3
_CATEGORY_LABELS = {
    "zen_free_quota": "حصة Zen المجانية",
    "quota": "حصة/معدل المزوّد",
    "rate_limit": "تحديد المعدل",
    "timeout": "انتهاء المهلة",
    "transport": "اتصال بالوكيل",
    "provider": "خطأ المزوّد",
    "unknown": "غير محدد",
}


def _bytes_label(value: float) -> str:
    return f"{value / _GIB:.1f} GiB"


def _category_label(name: str) -> str:
    return _CATEGORY_LABELS.get(name, name)


def render_metrics(snapshot: Any) -> str:
    """Format a :class:`BridgeMetricsService.MetricsSnapshot` for Telegram.

    Pure function of the snapshot: no I/O, no clock reads, so the layout is
    fully covered by unit tests. Units are spelled out (s / GiB / %) because
    these numbers are the first thing the owner compares against a dashboard.
    """
    queue = snapshot.queue
    outcomes = snapshot.outcomes
    lines = [
        f"قياسات الجسر — آخر {snapshot.window_hours} ساعة",
        "",
        "الطابور:",
        f"• بالانتظار الآن: {queue.depth} (جاهز {queue.queued} • مؤجّل {queue.retrying})",
        f"• قيد التنفيذ: {queue.active}"
        + (f" من حد {snapshot.worker_limit} عامل" if snapshot.worker_limit is not None else ""),
    ]
    wait_p50 = getattr(queue, "wait_p50_seconds", None)
    wait_p95 = getattr(queue, "wait_p95_seconds", None)
    if wait_p50 is not None:
        line = f"• انتظار قبل البدء: p50 {wait_p50:.1f}s"
        if wait_p95 is not None:
            line += f" • p95 {wait_p95:.1f}s"
        line += f" ({int(getattr(queue, 'wait_samples', 0))} مهمة)"
        lines.append(line)
    lines.extend([
        f"• مجدولة future: {queue.scheduled}",
        f"• منتهية: {queue.completed} • فاشلة: {queue.failed} • ملغاة: {queue.cancelled}",
        "",
        "النتائج داخل النافذة:",
        f"• مهام مكتملة: {outcomes.finished} (ناجحة {outcomes.succeeded} • فاشلة {outcomes.failed})",
        f"• مؤجّلة بسبب المزوّد: {outcomes.deferred}",
    ])
    if outcomes.avg_duration_seconds is not None:
        lines.append(
            f"• متوسط زمن التنفيذ: {outcomes.avg_duration_seconds:.1f}s"
            f" (الأطول {outcomes.max_duration_seconds:.1f}s)"
        )
    else:
        lines.append("• متوسط زمن التنفيذ: لا توجد مهام ناجحة في النافذة")
    if outcomes.deferral_categories:
        breakdown = " • ".join(
            f"{_category_label(name)} {count}" for name, count in outcomes.deferral_categories
        )
        lines.append(f"• أسباب التأجيل: {breakdown}")
    resources = snapshot.resources or {}
    if resources:
        lines.append("")
        lines.append("موارد المضيف:")
        if "memory_bytes" in resources:
            lines.append(f"• الذاكرة الكلية: {_bytes_label(resources['memory_bytes'])}")
        if "cpu_percent" in resources:
            lines.append(f"• معالجCPU: {resources['cpu_percent']:.1f}%")
        if "disk_free_bytes" in resources:
            lines.append(f"• قرص حر: {_bytes_label(resources['disk_free_bytes'])}")
    lines.append("")
    lines.append(f"وقت القياس: {snapshot.sampled_at:%Y-%m-%d %H:%M:%S} UTC")
    return "\n".join(lines)
