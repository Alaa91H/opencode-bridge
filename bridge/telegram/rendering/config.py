"""Telegram rendering for safe bridge configuration."""

from __future__ import annotations

import json
from typing import Any


def _pretty(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )


def render_config(payload: dict[str, Any]) -> str:
    return "الإعدادات الفعلية غير السرية\n\n" + _pretty(payload)


def render_limits(payload: dict[str, Any]) -> str:
    lines = ["الحدود الفعلية الحالية"]
    labels = {
        "attachment_max_bytes": "حجم المرفق الأقصى",
        "attachment_max_count": "عدد المرفقات الأقصى",
        "attachment_max_total_bytes": "إجمالي حجم المرفقات",
        "attachment_pending_seconds": "مهلة المرفقات المعلّقة",
        "media_group_debounce_seconds": "مهلة تجميع الألبوم",
        "task_workers": "الحد الإداري للـworkers",
        "task_poll_seconds": "فاصل فحص الطابور",
        "worker_recovery_seconds": "مهلة تعافي workers",
    }
    for key, value in payload.items():
        lines.append(f"• {labels.get(key, key)}: {value}")
    return "\n".join(lines)
