"""Persistent local accounting for OpenCode free-tier request points.

OpenCode's upstream free quota does not currently expose a reliable remaining
counter to clients. This tracker therefore records the model requests observed
by this bridge and presents a clearly labelled local estimate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


@dataclass(frozen=True)
class FreePointsSnapshot:
    day: str
    limit: int
    used: int
    remaining: int
    exhausted: bool


class FreePointsTracker:
    def __init__(self, path: Path, daily_limit: int = 200, timezone_name: str = "Europe/Berlin") -> None:
        self.path = Path(path)
        self.daily_limit = max(1, int(daily_limit))
        try:
            self.timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Invalid free-points timezone: {timezone_name}") from exc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS free_points_usage (
                    day TEXT PRIMARY KEY,
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    exhausted INTEGER NOT NULL DEFAULT 0 CHECK (exhausted IN (0, 1)),
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _day(self, now: datetime | None = None) -> str:
        value = now or datetime.now(UTC)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone).date().isoformat()

    def snapshot(self, now: datetime | None = None) -> FreePointsSnapshot:
        day = self._day(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT used, exhausted FROM free_points_usage WHERE day = ?",
                (day,),
            ).fetchone()
        used = int(row[0]) if row else 0
        exhausted = bool(row[1]) if row else False
        remaining = 0 if exhausted else max(0, self.daily_limit - used)
        return FreePointsSnapshot(day, self.daily_limit, used, remaining, exhausted)

    def record(self, points: int, now: datetime | None = None) -> FreePointsSnapshot:
        amount = max(0, int(points))
        day = self._day(now)
        updated_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO free_points_usage(day, used, exhausted, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(day) DO UPDATE SET
                    used = free_points_usage.used + excluded.used,
                    updated_at = excluded.updated_at
                """,
                (day, amount, updated_at),
            )
            row = connection.execute(
                "SELECT used, exhausted FROM free_points_usage WHERE day = ?",
                (day,),
            ).fetchone()
        used = int(row[0]) if row else amount
        exhausted = bool(row[1]) if row else False
        remaining = 0 if exhausted else max(0, self.daily_limit - used)
        return FreePointsSnapshot(day, self.daily_limit, used, remaining, exhausted)

    def mark_exhausted(self, now: datetime | None = None) -> FreePointsSnapshot:
        day = self._day(now)
        updated_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO free_points_usage(day, used, exhausted, updated_at)
                VALUES (?, 0, 1, ?)
                ON CONFLICT(day) DO UPDATE SET
                    exhausted = 1,
                    updated_at = excluded.updated_at
                """,
                (day, updated_at),
            )
            row = connection.execute(
                "SELECT used FROM free_points_usage WHERE day = ?",
                (day,),
            ).fetchone()
        used = int(row[0]) if row else 0
        return FreePointsSnapshot(day, self.daily_limit, used, 0, True)


def format_free_points_header(snapshot: FreePointsSnapshot, command_points: int) -> str:
    """Render exactly two operator-facing lines for Telegram output."""
    used = max(0, int(command_points))
    unit = "نقطة" if used == 1 else "نقاط"
    return (
        f"النقاط المجانية المتبقية اليوم (تقديري): {snapshot.remaining}/{snapshot.limit}\n"
        f"استهلاك هذا الأمر: {used} {unit}"
    )
