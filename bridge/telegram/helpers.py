"""Helper functions extracted from the bot orchestrator.

These are pure or near-pure helpers that do not depend on the global service
instances owned by ``bot.py``. Moving them here keeps ``bot.py`` focused on
wiring and lifecycle while centralising small reusable utilities.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bridge.domain.schedules import parse_utc_datetime as schedule_parse_utc_datetime


def task_status_text(task: Any) -> str:
    """Convert a queued task's status to user-facing Arabic text."""
    labels = {
        "queued": "بانتظار التنفيذ",
        "scheduled": "مجدولة",
        "running": "قيد التنفيذ",
        "completed": "مكتملة",
        "failed": "فشلت",
        "cancelled": "ملغاة",
    }
    return labels.get(getattr(task, "status", ""), str(getattr(task, "status", "")))


def parse_utc_datetime(value: str) -> datetime:
    """Parse a UTC datetime string using the T02 scheduling domain parser."""
    return schedule_parse_utc_datetime(value)


def utc_now() -> datetime:
    """Return the current UTC datetime (centralised for testability)."""
    return datetime.now(UTC)
