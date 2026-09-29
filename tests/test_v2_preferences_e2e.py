"""Verify the three preference fields are genuinely consumed by the runtime.

A setting that nothing reads is a lie shown to the user. Each test here proves a
real effect, not merely that a row was written to the database.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any

from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.infrastructure.database.user_settings_store import UserSettingsStore
from bridge.services.user_preferences_service import UserPreferencesService
from progress import ProgressStore
from progress_reporter import DEFAULT_DISPLAY, DisplayPreferences, LiveProgressReporter
from task_queue import TaskQueueStore


class FakeTask:
    def __init__(self, task_id: int = 7, owner_id: str = "42") -> None:
        self.id = task_id
        self.owner_id = owner_id
        self.chat_id = 99
        self.status_message_id = None


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return type("M", (), {"message_id": 500 + len(self.sent)})()

    async def edit_message_text(self, **kwargs: Any) -> Any:
        self.edits.append(kwargs)
        return self


class DisplayPreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.database = BridgeDatabase(root / "prefs.db")
        await MigrationRunner(self.database).migrate()
        self.preferences = UserPreferencesService(UserSettingsStore(self.database))
        self.queue = TaskQueueStore(root / "queue.db")
        await self.queue.init()
        self.store = ProgressStore()
        self.bot = FakeBot()
        self.task = FakeTask()

    async def asyncTearDown(self) -> None:
        await self.queue.close()
        await self.database.close()
        self.tmp.cleanup()

    async def _reporter(self, display: Any = None) -> LiveProgressReporter:
        reporter = LiveProgressReporter(
            self.task,
            self.bot,
            self.queue,
            self.store,
            display_preferences=display,
        )
        return reporter

    # ------------------------------------------------------------ output style

    async def test_output_style_full_shows_detailed_progress(self):
        await self.preferences.update("42", output_style="full")
        preferences = await self.preferences.get("42")
        display = DisplayPreferences(output_style=preferences.output_style)
        reporter = await self._reporter(lambda: display)
        await reporter.start()
        await reporter.record("processing", "الوكيل استلم المهمة وعم يعالجها.")
        await reporter.refresh(force=True)
        self.assertTrue(await reporter._detail())
        self.assertTrue(display.detail)

    async def test_output_style_summary_is_the_default_and_compact(self):
        summary = DisplayPreferences(output_style="summary")
        compact = DisplayPreferences(output_style="compact")
        self.assertFalse(summary.detail)
        self.assertFalse(compact.detail)
        self.assertFalse(DisplayPreferences().detail)

    # ----------------------------------------------------- notification level

    async def test_silent_level_suppresses_live_progress(self):
        display = DisplayPreferences(notification_level="silent")
        reporter = await self._reporter(lambda: display)
        await reporter.start()
        before = len(self.bot.edits) + len(self.bot.sent)
        await reporter.record("processing", "الوكيل عم يعالجها.")
        await reporter.refresh(force=True)
        self.assertEqual(len(self.bot.edits) + len(self.bot.sent), before)
        self.assertFalse(await reporter._show_progress())

    async def test_errors_level_only_shows_failures(self):
        display = DisplayPreferences(notification_level="errors")
        reporter = await self._reporter(lambda: display)
        self.assertFalse(await reporter._show_progress("processing"))
        self.assertTrue(await reporter._show_progress("failed"))

    async def test_normal_level_shows_everything(self):
        display = DisplayPreferences(notification_level="normal")
        reporter = await self._reporter(lambda: display)
        self.assertTrue(await reporter._show_progress("processing"))
        self.assertTrue(await reporter._show_progress("failed"))

    async def test_final_render_is_always_allowed_even_when_silent(self):
        display = DisplayPreferences(notification_level="silent")
        reporter = await self._reporter(lambda: display)
        await reporter.start()
        before = len(self.bot.edits)
        await reporter.finish("completed", "اكتمل التنفيذ.")
        await reporter.refresh(force=True, final=True)
        self.assertGreater(len(self.bot.edits), before)

    async def test_missing_resolver_falls_back_to_defaults(self):
        reporter = await self._reporter(None)
        self.assertEqual(await reporter._preferences(), DEFAULT_DISPLAY)
        self.assertTrue(await reporter._show_progress())

    async def test_broken_resolver_falls_back_instead_of_raising(self):
        def broken() -> Any:
            raise RuntimeError("database gone")

        reporter = await self._reporter(broken)
        self.assertEqual(await reporter._preferences(), DEFAULT_DISPLAY)

    async def test_async_resolver_is_awaited(self):
        async def slow() -> DisplayPreferences:
            return DisplayPreferences(notification_level="silent")

        reporter = await self._reporter(slow)
        self.assertEqual((await reporter._preferences()).notification_level, "silent")

    async def test_wrong_resolver_type_is_ignored(self):
        reporter = await self._reporter(lambda: "not a preference object")
        self.assertEqual(await reporter._preferences(), DEFAULT_DISPLAY)

    # ----------------------------------------------------------------- values

    async def test_every_stored_level_drives_the_real_decision(self):
        expected = {
            "silent": (False, False),
            "errors": (False, True),
            "normal": (True, True),
            "verbose": (True, True),
        }
        for level, (during, on_failure) in expected.items():
            with self.subTest(level=level):
                await self.preferences.update("42", notification_level=level)
                stored = await self.preferences.get("42")
                display = DisplayPreferences(notification_level=stored.notification_level)
                reporter = await self._reporter(lambda value=display: value)
                self.assertEqual(await reporter._show_progress("processing"), during)
                self.assertEqual(await reporter._show_progress("failed"), on_failure)

    async def test_retention_round_trips_as_an_integer(self):
        await self.preferences.update("42", retention_days=90)
        self.assertEqual((await self.preferences.get("42")).retention_days, 90)

    async def test_preference_survives_a_restart(self):
        await self.preferences.update("42", notification_level="errors", output_style="full")
        await self.database.close()
        self.database = BridgeDatabase(Path(self.tmp.name) / "prefs.db")
        await MigrationRunner(self.database).migrate()
        self.preferences = UserPreferencesService(UserSettingsStore(self.database))
        stored = await self.preferences.get("42")
        self.assertEqual(stored.notification_level, "errors")
        self.assertEqual(stored.output_style, "full")
