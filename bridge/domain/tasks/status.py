"""Pure task status rendering labels."""

from __future__ import annotations

from typing import Any


def task_status_text(task: Any) -> str:
    labels = {
        "queued": "بانتظار التنفيذ",
        "scheduled": "مجدولة",
        "running": "قيد التنفيذ",
        "completed": "مكتملة",
        "failed": "فشلت",
        "cancelled": "ملغاة",
    }
    return labels.get(task.status, task.status)
