"""Regression tests for the panel display-preference wiring.

The bug
-------
``bot._display_preferences`` referenced ``DisplayPreferences`` without importing
it. ``LiveProgressReporter._preferences`` catches every exception and falls back
to defaults, so the missing name was never a visible failure: the bot kept
running while the owner's saved ``notification_level`` and ``output_style`` were
silently discarded on every task, with only an ``info`` line naming the
exception type.

These tests pin name resolution and the value that reaches the reporter. They
deliberately avoid the real database so the module exits cleanly; ruff's F821
already covers undefined names correctly across the tree, so this file does not
reimplement a scope checker.
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys
import unittest
from typing import Any

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import bot
from progress_reporter import (
    DEFAULT_DISPLAY,
    DisplayPreferences,
    LiveProgressReporter,
)

SOURCE = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")


class FakePreferences:
    def __init__(self, level: str, style: str) -> None:
        self.notification_level = level
        self.output_style = style


class DisplayPreferencesNameTests(unittest.TestCase):
    def test_symbol_is_imported_into_bot(self) -> None:
        self.assertTrue(
            hasattr(bot, "DisplayPreferences"),
            "bot.DisplayPreferences is missing; the reporter swallows the "
            "NameError and silently discards the owner's display preferences",
        )

    def test_import_line_brings_both_names(self) -> None:
        self.assertIn(
            "from progress_reporter import DisplayPreferences, LiveProgressReporter",
            SOURCE,
        )

    def test_helper_is_a_coroutine_function(self) -> None:
        # The delivery adapter awaits this hook, so it must stay async.
        # inspect.iscoroutinefunction is the supported spelling: asyncio's copy is
        # deprecated and is scheduled for removal in Python 3.16, which CI already
        # turns into a test error via PYTHONWARNINGS=error::DeprecationWarning.
        self.assertTrue(inspect.iscoroutinefunction(bot._display_preferences))


class DisplayPreferencesWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = bot._user_preferences

    def tearDown(self) -> None:
        bot._user_preferences = self._original

    def test_saved_preferences_reach_the_reporter(self) -> None:
        class Stub:
            async def get(self, owner_id: str) -> FakePreferences:
                self.owner_id = owner_id
                return FakePreferences("errors", "concise")

        bot._user_preferences = lambda: Stub()  # type: ignore[assignment]
        value = asyncio.run(bot._display_preferences("42"))
        self.assertIsInstance(value, DisplayPreferences)
        self.assertEqual(value.notification_level, "errors")
        self.assertEqual(value.output_style, "concise")

    def test_read_failure_returns_usable_defaults(self) -> None:
        def boom() -> Any:
            raise RuntimeError("database is locked")

        bot._user_preferences = boom  # type: ignore[assignment]
        value = asyncio.run(bot._display_preferences("42"))
        self.assertIsInstance(value, DisplayPreferences)
        self.assertEqual(value.notification_level, DEFAULT_DISPLAY.notification_level)
        self.assertEqual(value.output_style, DEFAULT_DISPLAY.output_style)


class ReporterFallbackTests(unittest.TestCase):
    def _reporter(self, hook: Any) -> LiveProgressReporter:
        reporter = LiveProgressReporter.__new__(LiveProgressReporter)
        reporter._display = hook
        return reporter

    def test_falls_back_when_the_hook_raises(self) -> None:
        def boom() -> Any:
            raise NameError("name 'DisplayPreferences' is not defined")

        self.assertIs(asyncio.run(self._reporter(boom)._preferences()), DEFAULT_DISPLAY)

    def test_absent_hook_falls_back(self) -> None:
        self.assertIs(asyncio.run(self._reporter(None)._preferences()), DEFAULT_DISPLAY)

    def test_chosen_preferences_are_returned(self) -> None:
        chosen = DisplayPreferences(notification_level="max", output_style="concise")
        self.assertIs(asyncio.run(self._reporter(lambda: chosen)._preferences()), chosen)

    def test_async_hook_is_awaited(self) -> None:
        chosen = DisplayPreferences(notification_level="silent", output_style="full")

        async def hook() -> DisplayPreferences:
            return chosen

        self.assertIs(asyncio.run(self._reporter(hook)._preferences()), chosen)

    def test_wrong_type_falls_back(self) -> None:
        self.assertIs(asyncio.run(self._reporter(lambda: object())._preferences()), DEFAULT_DISPLAY)


if __name__ == "__main__":
    unittest.main()
