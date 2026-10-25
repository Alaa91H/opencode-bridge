"""Owner-scoped reminders with durable scheduling and delivery attempts."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bridge.infrastructure.database.sqlite import BridgeDatabase
from task_queue import decode_time, encode_time

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 1_000
MAX_REMINDERS = 20
MAX_ATTEMPTS = 3
POLL_SECONDS = 30.0
MAX_HORIZON = timedelta(days=365)
DEFAULT_DELAY = timedelta(hours=1)

_INTERVAL_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhd])$", re.IGNORECASE)
_TIME_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)$")


class ReminderInputError(ValueError):
    """A reminder request that can be explained to the owner and corrected."""


@dataclass(frozen=True, slots=True)
class ReminderRequest:
    message: str
    remind_at: datetime


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    owner_id: str
    chat_id: int
    message: str
    remind_at: datetime
    status: str
    created_at: datetime
    sent_at: datetime | None
    attempts: int
    last_error: str | None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_interval(text: str, now: datetime) -> datetime:
    match = _INTERVAL_RE.match(text.strip())
    if match is None:
        raise ReminderInputError("صيغة المهلة غير صحيحة. مثال: in 10m أو in 2h.")
    amount = int(match.group("value"))
    unit = match.group("unit").lower()
    if amount <= 0:
        raise ReminderInputError("يجب أن تكون المهلة أكبر من صفر.")
    multipliers = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
    remind_at = now + timedelta(seconds=amount * multipliers[unit])
    if remind_at - now > MAX_HORIZON:
        raise ReminderInputError("أقصى مدة للتذكير هي سنة واحدة.")
    return remind_at


def _parse_at(tokens: Sequence[str], now: datetime) -> tuple[datetime, int]:
    if not tokens:
        raise ReminderInputError("أضف وقتاً بعد at. مثال: at 18:30 أو at 2026-10-25 09:00.")
    date_token = tokens[0]
    time_token = None
    consumed = 1
    if _TIME_RE.match(date_token):
        time_token = date_token
    elif len(tokens) > 1 and _TIME_RE.match(tokens[1]):
        time_token = tokens[1]
        consumed = 2
    else:
        raise ReminderInputError("صيغة الوقت غير صحيحة. مثال: at 18:30 أو at 2026-10-25 09:00.")

    hour, minute = (int(part) for part in time_token.split(":"))
    if date_token == time_token:
        candidate = datetime(now.year, now.month, now.day, hour, minute, tzinfo=now.tzinfo)
        if candidate <= now:
            candidate += timedelta(days=1)
    else:
        try:
            day = datetime.strptime(date_token, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ReminderInputError(
                "صيغة التاريخ غير صحيحة. استخدم YYYY-MM-DD HH:MM."
            ) from exc
        candidate = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(
            hour=hour, minute=minute
        )
        if candidate <= now:
            raise ReminderInputError("لا يمكن جدولة تذكير في وقت مضى.")
    if candidate - now > MAX_HORIZON:
        raise ReminderInputError("أقصى مدة للتذكير هي سنة واحدة.")
    return candidate, consumed


def parse_reminder_args(args: Sequence[str], *, now: datetime) -> ReminderRequest:
    """Parse `/remind` arguments without guessing inside the message body."""
    reference = _aware(now)
    tokens = [str(part).strip() for part in args if str(part).strip()]
    if not tokens:
        raise ReminderInputError(
            "استخدم: /remind in 10m نص التذكير، أو /remind at 18:30 نص التذكير."
        )

    remind_at: datetime | None = None
    first = tokens[0].lower()
    if first.startswith("in:"):
        remind_at = _parse_interval(tokens[0][3:], reference)
        tokens = tokens[1:]
    elif first == "in":
        if len(tokens) < 2:
            raise ReminderInputError("أضف مهلة بعد in. مثال: in 10m.")
        remind_at = _parse_interval(tokens[1], reference)
        tokens = tokens[2:]
    elif first.startswith("at:"):
        remind_at, consumed = _parse_at([tokens[0][3:]], reference)
        tokens = tokens[consumed:]
    elif first == "at":
        remind_at, consumed = _parse_at(tokens[1:], reference)
        tokens = tokens[consumed + 1:]

    if remind_at is None:
        remind_at = reference + DEFAULT_DELAY
        if remind_at - reference > MAX_HORIZON:
            raise ReminderInputError("أقصى مدة للتذكير هي سنة واحدة.")

    message = " ".join(tokens).strip()
    if not message:
        raise ReminderInputError("أضف نص التذكير بعد الوقت.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ReminderInputError(f"نص التذكير أطول من {MAX_MESSAGE_LENGTH} حرف.")
    return ReminderRequest(message=message, remind_at=remind_at)


class ReminderStore:
    def __init__(self, database: BridgeDatabase) -> None:
        self.database = database

    async def _connection(self):
        return await self.database.connect()

    @staticmethod
    def _from_row(row) -> Reminder:
        remind_at = decode_time(row["remind_at"])
        created_at = decode_time(row["created_at"])
        if remind_at is None or created_at is None:
            raise ValueError("reminder row has no timestamps")
        return Reminder(
            id=int(row["id"]),
            owner_id=str(row["owner_id"]),
            chat_id=int(row["chat_id"]),
            message=str(row["message"]),
            remind_at=remind_at,
            status=str(row["status"]),
            created_at=created_at,
            sent_at=decode_time(row["sent_at"]),
            attempts=int(row["attempts"]),
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )

    async def create(self, owner_id: str, chat_id: int, message: str, remind_at: datetime) -> int:
        now = encode_time(datetime.now(UTC))
        async with self.database.transaction(immediate=True) as db:
            cursor = await db.execute(
                """
                INSERT INTO reminders (owner_id, chat_id, message, remind_at, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (str(owner_id), int(chat_id), message, encode_time(remind_at), now),
            )
            return int(cursor.lastrowid or 0)

    async def list_for_owner(self, owner_id: str, limit: int = MAX_REMINDERS) -> list[Reminder]:
        db = await self._connection()
        async with self.database.transaction_lock:
            async with db.execute(
                """
                SELECT * FROM reminders
                WHERE owner_id = ? AND status = 'pending'
                ORDER BY remind_at ASC, id ASC LIMIT ?
                """,
                (str(owner_id), max(1, min(int(limit), 100))),
            ) as cursor:
                rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    async def cancel(self, reminder_id: int, owner_id: str) -> bool:
        async with self.database.transaction(immediate=True) as db:
            cursor = await db.execute(
                """
                UPDATE reminders SET status = 'cancelled'
                WHERE id = ? AND owner_id = ? AND status = 'pending'
                """,
                (int(reminder_id), str(owner_id)),
            )
            return bool(cursor.rowcount)

    async def reset_interrupted(self) -> int:
        async with self.database.transaction(immediate=True) as db:
            cursor = await db.execute(
                "UPDATE reminders SET status = 'pending' WHERE status = 'sending'"
            )
            return int(cursor.rowcount or 0)

    async def claim_due(self, now: datetime, limit: int = 10) -> list[Reminder]:
        claimed: list[Reminder] = []
        async with self.database.transaction(immediate=True) as db:
            async with db.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending' AND remind_at <= ?
                ORDER BY remind_at ASC, id ASC LIMIT ?
                """,
                (encode_time(now), max(1, min(int(limit), 100))),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                reminder = self._from_row(row)
                updated = await db.execute(
                    "UPDATE reminders SET status = 'sending' WHERE id = ? AND status = 'pending'",
                    (reminder.id,),
                )
                if updated.rowcount:
                    claimed.append(reminder)
        return claimed

    async def mark_sent(self, reminder_id: int) -> None:
        async with self.database.transaction(immediate=True) as db:
            await db.execute(
                """
                UPDATE reminders SET status = 'sent', sent_at = ?, last_error = NULL
                WHERE id = ? AND status = 'sending'
                """,
                (encode_time(datetime.now(UTC)), int(reminder_id)),
            )

    async def mark_attempt_failed(self, reminder_id: int, error: str) -> None:
        safe_error = " ".join(str(error).split())[:500]
        async with self.database.transaction(immediate=True) as db:
            async with db.execute(
                "SELECT attempts FROM reminders WHERE id = ? AND status = 'sending'",
                (int(reminder_id),),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return
            attempts = int(row["attempts"]) + 1
            status = "pending" if attempts < MAX_ATTEMPTS else "failed"
            await db.execute(
                """
                UPDATE reminders SET attempts = ?, status = ?, last_error = ?
                WHERE id = ? AND status = 'sending'
                """,
                (attempts, status, safe_error, int(reminder_id)),
            )


SendMessage = Callable[[int, str], Awaitable[None]]


class ReminderScheduler:
    def __init__(
        self,
        store: ReminderStore,
        send: SendMessage,
        *,
        poll_seconds: float = POLL_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self.store = store
        self.send = send
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.log = logger or log
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="reminder-scheduler")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_once(self, now: datetime | None = None) -> int:
        reference = _aware(now or datetime.now(UTC))
        delivered = 0
        for reminder in await self.store.claim_due(reference):
            try:
                await self.send(reminder.chat_id, f"تذكير: {reminder.message}")
            except Exception as exc:
                self.log.warning("reminder %s delivery failed: %s", reminder.id, exc)
                await self.store.mark_attempt_failed(reminder.id, str(exc))
            else:
                await self.store.mark_sent(reminder.id)
                delivered += 1
        return delivered

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("reminder scheduler tick failed")
            await asyncio.sleep(self.poll_seconds)
