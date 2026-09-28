"""Pure scheduler-v2 recurrence and policy primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class MisfirePolicy(str, Enum):
    SKIP = "skip"
    RUN_ONCE = "run_once"
    CATCH_UP = "catch_up"
    COALESCE = "coalesce"


class OverlapPolicy(str, Enum):
    FORBID = "forbid"
    ALLOW = "allow"
    REPLACE = "replace"
    QUEUE = "queue"


@dataclass(frozen=True)
class Recurrence:
    kind: str
    cron: str | None = None
    timezone_name: str = "UTC"
    interval_seconds: int | None = None
    hour: int | None = None
    minute: int = 0
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None

    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {self.timezone_name}") from exc

    def next_after(self, after: datetime) -> datetime | None:
        if self.kind == "once":
            return None
        if after.tzinfo is None:
            after = after.replace(tzinfo=UTC)
        if self.kind == "cron":
            fields = (self.cron or "").split()
            if len(fields) != 5:
                raise ValueError("cron requires five fields")
            minute_f, hour_f, dom_f, month_f, dow_f = fields
            def matches(value: int, field: str) -> bool:
                if field == "*":
                    return True
                return value in {int(v) for v in field.split(",")}
            zone = self.timezone()
            cursor = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
            for _ in range(60 * 24 * 366):
                local = cursor.astimezone(zone)
                cron_dow = (local.weekday() + 1) % 7
                if (matches(local.minute, minute_f) and matches(local.hour, hour_f)
                    and matches(local.day, dom_f) and matches(local.month, month_f)
                    and matches(cron_dow, dow_f)):
                    return cursor
                cursor += timedelta(minutes=1)
            raise ValueError("No cron occurrence within one year")
        if self.kind == "interval":
            if not self.interval_seconds or self.interval_seconds <= 0:
                raise ValueError("interval_seconds must be positive")
            return after.astimezone(UTC) + timedelta(seconds=self.interval_seconds)

        zone = self.timezone()
        local = after.astimezone(zone)
        hour = self.hour if self.hour is not None else local.hour
        for offset in range(1, 370):
            day = local.date() + timedelta(days=offset if self.kind != "daily" else offset)
            if self.kind in {"weekly", "weekdays"} and self.weekdays and day.weekday() not in self.weekdays:
                continue
            if self.kind == "monthly" and day.day != (self.day_of_month or 1):
                continue
            candidate = datetime(day.year, day.month, day.day, hour, self.minute, tzinfo=zone)
            return candidate.astimezone(UTC)
        raise ValueError("No next recurrence within one year")


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {name}") from exc
    return name


def due_occurrences(
    scheduled_at: datetime, now: datetime, recurrence: Recurrence, policy: MisfirePolicy, limit: int = 100
) -> list[datetime]:
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if scheduled_at > now:
        return []
    if policy == MisfirePolicy.SKIP:
        return []
    if policy in {MisfirePolicy.RUN_ONCE, MisfirePolicy.COALESCE}:
        return [scheduled_at]
    result: list[datetime] = []
    cursor: datetime | None = scheduled_at
    while cursor is not None and cursor <= now and len(result) < max(1, limit):
        result.append(cursor)
        cursor = recurrence.next_after(cursor)
    return result
