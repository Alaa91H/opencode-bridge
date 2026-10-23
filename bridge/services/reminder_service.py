"""Professional reminder/notification service tied to tasks, users, and server.

Supports:
- Creating reminders linked to a task or independent.
- Scheduling by exact datetime (with UTC storage).
- Sending via an injected Telegram transport (bot.send_message or adapter).
- Reordering / cancelling / listing by owner.
- Integration with the sequential command queue (reminder can trigger next).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from bridge.infrastructure.database.sqlite import BridgeDatabase

log = logging.getLogger("opencode_bridge.reminders")


class ReminderTransport(Protocol):
    async def send_text(self, destination: str | int, text: str) -> Any: ...


@dataclass
class Reminder:
    id: int
    owner_id: str
    message: str
    remind_at: datetime
    status: str = "pending"  # pending, sent, failed, cancelled
    task_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sent_at: datetime | None = None


class ReminderStore:
    """Durable reminder storage using BridgeDatabase (same SQLite as tasks)."""

    def __init__(self, database: BridgeDatabase) -> None:
        self.db = database
        self._lock = database.transaction_lock

    async def init(self) -> None:
        db = await self.db.connect()
        async with self.db.transaction(immediate=True):
            await db.execute(
                """CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    task_id INTEGER,
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, remind_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_owner ON reminders(owner_id, remind_at)"
            )
            await db.commit()

    async def create(
        self,
        owner_id: str,
        message: str,
        remind_at: datetime,
        task_id: int | None = None,
    ) -> int:
        db = await self.db.connect()
        async with self._lock:
            cursor = await db.execute(
                """INSERT INTO reminders (owner_id, message, remind_at, status, task_id, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (str(owner_id), str(message), remind_at.isoformat(), task_id, datetime.now(UTC).isoformat()),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def due_before(self, now: datetime) -> list[Reminder]:
        db = await self.db.connect()
        async with self._lock:
            async with db.execute(
                "SELECT * FROM reminders WHERE status='pending' AND remind_at <= ? ORDER BY remind_at ASC",
                (now.isoformat(),),
            ) as cursor:
                rows = await cursor.fetchall()
        result: list[Reminder] = []
        for row in rows:
            result.append(Reminder(
                id=int(row["id"]),
                owner_id=str(row["owner_id"]),
                message=str(row["message"]),
                remind_at=datetime.fromisoformat(str(row["remind_at"])),
                status=str(row["status"]),
                task_id=int(row["task_id"]) if row["task_id"] is not None else None,
                created_at=datetime.fromisoformat(str(row["created_at"])),
                sent_at=datetime.fromisoformat(str(row["sent_at"])) if row["sent_at"] else None,
            ))
        return result

    async def mark_sent(self, reminder_id: int) -> None:
        db = await self.db.connect()
        async with self._lock:
            await db.execute(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), reminder_id),
            )
            await db.commit()

    async def cancel(self, reminder_id: int, owner_id: str) -> bool:
        db = await self.db.connect()
        async with self._lock:
            async with db.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=? AND owner_id=?",
                (reminder_id, str(owner_id)),
            ) as cursor:
                changed = cursor.rowcount > 0
            await db.commit()
            return changed

    async def list_for_owner(self, owner_id: str, limit: int = 50) -> list[Reminder]:
        db = await self.db.connect()
        async with self._lock:
            async with db.execute(
                "SELECT * FROM reminders WHERE owner_id=? ORDER BY remind_at DESC LIMIT ?",
                (str(owner_id), max(1, min(limit, 200))),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            Reminder(
                id=int(r["id"]),
                owner_id=str(r["owner_id"]),
                message=str(r["message"]),
                remind_at=datetime.fromisoformat(str(r["remind_at"])),
                status=str(r["status"]),
                task_id=int(r["task_id"]) if r["task_id"] is not None else None,
                created_at=datetime.fromisoformat(str(r["created_at"])),
                sent_at=datetime.fromisoformat(str(r["sent_at"])) if r["sent_at"] else None,
            ) for r in rows
        ]


class ReminderScheduler:
    """Poll due reminders at bounded intervals; send via transport; never fail silently."""

    def __init__(
        self,
        store: ReminderStore,
        transport: ReminderTransport,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.transport = transport
        self.poll_interval = max(5.0, float(poll_interval_seconds))
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="reminder-scheduler")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                now = datetime.now(UTC)
                due = await self.store.due_before(now)
                for reminder in due:
                    if reminder.status != "pending":
                        continue
                    try:
                        await self.transport.send_text(str(reminder.owner_id), f"⏰ تذكير: {reminder.message}")
                        await self.store.mark_sent(reminder.id)
                        log.info("sent reminder %s to %s", reminder.id, reminder.owner_id)
                    except Exception:
                        log.warning("failed to send reminder %s to %s", reminder.id, reminder.owner_id, exc_info=True)
                        # Keep pending for retry; do not drop.
            except Exception:
                log.exception("reminder loop error")
            await asyncio.sleep(self.poll_interval)


def create_reminder_service(database: BridgeDatabase, transport: ReminderTransport) -> tuple[ReminderStore, ReminderScheduler]:
    store = ReminderStore(database)
    scheduler = ReminderScheduler(store, transport)
    return store, scheduler
