"""Reminder parsing, persistence, delivery, and command contract tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.services.reminder_service import (
    ReminderInputError,
    ReminderScheduler,
    ReminderStore,
    parse_reminder_args,
)
from bridge.telegram.commands.reminders import ReminderCommands

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


class FakeReply:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, update, text: str) -> None:
        self.messages.append(text)


class FakeUpdate:
    def __init__(self, owner_id: int = 8708017146, chat_id: int = 8708017146) -> None:
        self.effective_user = type("User", (), {"id": owner_id})()
        self.effective_chat = type("Chat", (), {"id": chat_id})()


class FakeContext:
    def __init__(self, args: list[str]) -> None:
        self.args = args


class ReminderParserTests(unittest.TestCase):
    def test_defaults_to_one_hour_when_only_text_is_given(self) -> None:
        request = parse_reminder_args(["اتصل", "بالمكتب"], now=NOW)
        self.assertEqual(request.message, "اتصل بالمكتب")
        self.assertEqual(request.remind_at, NOW + timedelta(hours=1))

    def test_parses_relative_minutes(self) -> None:
        request = parse_reminder_args(["in", "10m", "راجع", "الطابور"], now=NOW)
        self.assertEqual(request.remind_at, NOW + timedelta(minutes=10))
        self.assertEqual(request.message, "راجع الطابور")

    def test_parses_compact_relative_marker(self) -> None:
        request = parse_reminder_args(["in:2h", "اجتماع"], now=NOW)
        self.assertEqual(request.remind_at, NOW + timedelta(hours=2))

    def test_parses_time_later_today(self) -> None:
        request = parse_reminder_args(["at", "18:30", "أرسل", "التقرير"], now=NOW)
        self.assertEqual(request.remind_at, datetime(2026, 10, 25, 18, 30, tzinfo=UTC))
        self.assertEqual(request.message, "أرسل التقرير")

    def test_rolls_past_time_to_next_day(self) -> None:
        request = parse_reminder_args(["at", "09:00", "صيانة"], now=NOW)
        self.assertEqual(request.remind_at, datetime(2026, 10, 26, 9, 0, tzinfo=UTC))

    def test_parses_explicit_date(self) -> None:
        request = parse_reminder_args(["at", "2026-10-27", "09:15", "مراجعة"], now=NOW)
        self.assertEqual(request.remind_at, datetime(2026, 10, 27, 9, 15, tzinfo=UTC))
        self.assertEqual(request.message, "مراجعة")

    def test_rejects_past_explicit_date(self) -> None:
        with self.assertRaises(ReminderInputError):
            parse_reminder_args(["at", "2026-10-24", "09:15", "مراجعة"], now=NOW)

    def test_rejects_empty_message(self) -> None:
        with self.assertRaises(ReminderInputError):
            parse_reminder_args(["in", "10m"], now=NOW)

    def test_rejects_invalid_duration(self) -> None:
        with self.assertRaises(ReminderInputError):
            parse_reminder_args(["in", "soon", "نص"], now=NOW)

    def test_rejects_long_message(self) -> None:
        with self.assertRaises(ReminderInputError):
            parse_reminder_args(["x" * 1_001], now=NOW)


class ReminderStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = BridgeDatabase(Path(self._tmp.name) / "bridge.db")
        await MigrationRunner(self.database).migrate()
        self.store = ReminderStore(self.database)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def test_create_list_and_cancel_are_owner_scoped(self) -> None:
        reminder_id = await self.store.create("owner", 10, "نص", NOW + timedelta(hours=1))
        await self.store.create("other", 20, "آخر", NOW + timedelta(hours=2))
        listed = await self.store.list_for_owner("owner")
        self.assertEqual([item.id for item in listed], [reminder_id])
        self.assertFalse(await self.store.cancel(reminder_id, "other"))
        self.assertTrue(await self.store.cancel(reminder_id, "owner"))
        self.assertEqual(await self.store.list_for_owner("owner"), [])

    async def test_claim_due_is_atomic_and_marks_sending(self) -> None:
        due = await self.store.create("owner", 10, "الآن", NOW - timedelta(seconds=1))
        await self.store.create("owner", 10, "غداً", NOW + timedelta(days=1))
        claimed = await self.store.claim_due(NOW)
        self.assertEqual([item.id for item in claimed], [due])
        self.assertEqual(await self.store.claim_due(NOW), [])

    async def test_delivery_failure_retries_then_fails(self) -> None:
        reminder_id = await self.store.create("owner", 10, "نص", NOW - timedelta(seconds=1))
        for attempt in range(3):
            claimed = await self.store.claim_due(NOW)
            self.assertEqual(len(claimed), 1)
            await self.store.mark_attempt_failed(reminder_id, f"error {attempt}")
        db = await self.database.connect()
        async with db.execute(
            "SELECT status, attempts, last_error FROM reminders WHERE id = ?", (reminder_id,)
        ) as cursor:
            row = await cursor.fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 3)
        self.assertEqual(row["last_error"], "error 2")

    async def test_reset_interrupted_returns_sending_to_pending(self) -> None:
        await self.store.create("owner", 10, "نص", NOW - timedelta(seconds=1))
        self.assertEqual(len(await self.store.claim_due(NOW)), 1)
        self.assertEqual(await self.store.reset_interrupted(), 1)
        self.assertEqual(len(await self.store.claim_due(NOW)), 1)


class ReminderSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = BridgeDatabase(Path(self._tmp.name) / "bridge.db")
        await MigrationRunner(self.database).migrate()
        self.store = ReminderStore(self.database)
        self.sent: list[tuple[int, str]] = []

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    async def test_run_once_delivers_due_reminder_once(self) -> None:
        await self.store.create("owner", 10, "راجع الطابور", NOW - timedelta(seconds=1))
        scheduler = ReminderScheduler(self.store, self.send)
        self.assertEqual(await scheduler.run_once(NOW), 1)
        self.assertEqual(self.sent, [(10, "تذكير: راجع الطابور")])
        self.assertEqual(await scheduler.run_once(NOW), 0)

    async def test_failed_send_is_retried(self) -> None:
        await self.store.create("owner", 10, "نص", NOW - timedelta(seconds=1))

        async def fail(chat_id: int, text: str) -> None:
            raise RuntimeError("telegram down")

        scheduler = ReminderScheduler(self.store, fail)
        self.assertEqual(await scheduler.run_once(NOW), 0)
        self.assertEqual(await scheduler.run_once(NOW), 0)
        self.assertEqual(await scheduler.run_once(NOW), 0)
        self.assertEqual(await self.store.list_for_owner("owner"), [])


class ReminderCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = BridgeDatabase(Path(self._tmp.name) / "bridge.db")
        await MigrationRunner(self.database).migrate()
        self.store = ReminderStore(self.database)
        self.sent: list[tuple[int, str]] = []

        async def send(chat_id: int, text: str) -> None:
            self.sent.append((chat_id, text))

        self.scheduler = ReminderScheduler(self.store, send)
        self.reply = FakeReply()
        self.commands = ReminderCommands(
            self.store,
            self.scheduler,
            self.reply,
            now=lambda: NOW,
        )
        self.update = FakeUpdate()

    async def asyncTearDown(self) -> None:
        await self.scheduler.stop()
        await self.database.close()
        self._tmp.cleanup()

    async def test_remind_creates_and_reports_reminder(self) -> None:
        await self.commands.remind(
            self.update,
            FakeContext(["in", "10m", "راجع", "الاختبارات"]),
        )
        self.assertIn("تم جدولة التذكير #1", self.reply.messages[0])
        listed = await self.store.list_for_owner(str(self.update.effective_user.id))
        self.assertEqual(listed[0].message, "راجع الاختبارات")

    async def test_reminders_lists_and_cancel_removes(self) -> None:
        await self.store.create(
            str(self.update.effective_user.id), 10, "مهم", NOW + timedelta(hours=1)
        )
        await self.commands.reminders(self.update, FakeContext([]))
        self.assertIn("مهم", self.reply.messages[-1])
        await self.commands.cancel(self.update, FakeContext(["1"]))
        self.assertIn("أُلغي التذكير #1", self.reply.messages[-1])

    async def test_invalid_input_is_explained(self) -> None:
        await self.commands.remind(self.update, FakeContext([]))
        self.assertIn("تعذر إنشاء التذكير", self.reply.messages[0])

    async def test_cancel_rejects_non_numeric_id(self) -> None:
        await self.commands.cancel(self.update, FakeContext(["abc"]))
        self.assertIn("عدداً صحيحاً", self.reply.messages[0])


if __name__ == "__main__":
    unittest.main()
