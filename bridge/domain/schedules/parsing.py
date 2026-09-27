"""Pure schedule parsing/formatting without Telegram, SQLite, or OpenCode imports."""

from __future__ import annotations

import re
from datetime import datetime, timezone

UTC = timezone.utc


def split_pipe_args(raw: str, expected: int) -> list[str]:
    if expected < 1:
        raise ValueError("عدد الأجزاء المتوقع يجب أن يكون موجبًا")
    parts = [part.strip() for part in raw.split("|", expected - 1)]
    if len(parts) != expected or any(not part for part in parts):
        raise ValueError("صيغة الأمر غير مكتملة")
    return parts


def parse_utc_datetime(value: str) -> datetime:
    """Parse the documented UTC schedule format without guessing timezone."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("اكتب الوقت بصيغة UTC: YYYY-MM-DD HH:MM") from exc


def parse_interval_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([mhd])", value.strip().lower())
    if not match:
        raise ValueError("اكتب التكرار مثل 30m أو 2h أو 1d")
    amount, unit = int(match.group(1)), match.group(2)
    seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
    if seconds < 300:
        raise ValueError("أقصر تكرار مسموح هو 5m")
    return seconds


def format_interval(seconds: int | None) -> str:
    if not seconds:
        return "مرة واحدة"
    if seconds % 86400 == 0:
        return f"كل {seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"كل {seconds // 3600}h"
    if seconds % 60 == 0:
        return f"كل {seconds // 60}m"
    return f"كل {seconds} ثانية"
