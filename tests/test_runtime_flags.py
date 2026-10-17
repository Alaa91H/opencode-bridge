"""Runtime flag reads: strict startup gates vs fail-soft task paths."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from bridge.config import runtime_flags


class StrictFlagTests(unittest.TestCase):
    def test_environment_beats_dotenv_and_truthy_spellings_work(self) -> None:
        for value in ("1", "true", "YES", " on "):
            with self.subTest(value=value), patch.dict(os.environ, {"BRIDGE_FLAG_X": value}):
                self.assertTrue(runtime_flags.enabled("BRIDGE_FLAG_X"))
        with patch.dict(os.environ, {"BRIDGE_FLAG_X": "0"}), patch.object(
            runtime_flags, "_parse_env_file", return_value={"BRIDGE_FLAG_X": "1"}
        ) as parse:
            self.assertFalse(runtime_flags.enabled("BRIDGE_FLAG_X"))
        parse.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(
            runtime_flags, "_parse_env_file", return_value={"BRIDGE_FLAG_X": "on"}
        ):
            self.assertTrue(runtime_flags.enabled("BRIDGE_FLAG_X"))

    def test_missing_flag_uses_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            runtime_flags, "_parse_env_file", return_value={}
        ):
            self.assertFalse(runtime_flags.enabled("BRIDGE_FLAG_MISSING"))
            self.assertTrue(runtime_flags.enabled("BRIDGE_FLAG_MISSING", default=True))

    def test_malformed_value_raises_for_startup_gates(self) -> None:
        with patch.dict(os.environ, {"BRIDGE_FLAG_BAD": "ture"}):
            with self.assertRaises(ValueError):
                runtime_flags.enabled("BRIDGE_FLAG_BAD")


class LenientFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_flags._WARNED.discard("BRIDGE_FLAG_BAD")

    def tearDown(self) -> None:
        runtime_flags._WARNED.discard("BRIDGE_FLAG_BAD")

    def test_malformed_value_degrades_to_default_with_one_warning(self) -> None:
        with patch.dict(os.environ, {"BRIDGE_FLAG_BAD": "ture"}):
            with self.assertLogs("opencode_bridge.runtime_flags", level="WARNING") as captured:
                for _ in range(3):
                    self.assertFalse(runtime_flags.enabled_lenient("BRIDGE_FLAG_BAD"))
                    self.assertTrue(
                        runtime_flags.enabled_lenient("BRIDGE_FLAG_BAD", default=True)
                    )
        self.assertEqual(len(captured.records), 1)
        self.assertIn("BRIDGE_FLAG_BAD", captured.output[0])

    def test_valid_and_missing_values_pass_through(self) -> None:
        with patch.dict(os.environ, {"BRIDGE_FLAG_OK": "on"}):
            self.assertTrue(runtime_flags.enabled_lenient("BRIDGE_FLAG_OK"))
        with patch.dict(os.environ, {}, clear=True), patch.object(
            runtime_flags, "_parse_env_file", return_value={"BRIDGE_FLAG_OK": "0"}
        ):
            self.assertFalse(runtime_flags.enabled_lenient("BRIDGE_FLAG_OK", default=True))

    def test_dotenv_fallback_is_read_through_the_shared_parser(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            runtime_flags,
            "_parse_env_file",
            return_value={"BRIDGE_FLAG_FILE": "1"},
        ) as parse:
            self.assertTrue(runtime_flags.enabled_lenient("BRIDGE_FLAG_FILE"))
        parse.assert_called_once()


class ReporterFlagCachingTests(unittest.TestCase):
    """Flags are read once per task; a typo must not raise during construction."""

    def _construct(self, env: dict[str, str]):
        from types import SimpleNamespace

        from progress import ProgressStore
        from progress_reporter import LiveProgressReporter

        task = SimpleNamespace(id=5, owner_id="u", chat_id=7)
        with patch.dict(os.environ, env, clear=False):
            return LiveProgressReporter(task, object(), object(), ProgressStore())

    def test_malformed_flags_do_not_break_task_construction(self) -> None:
        reporter = self._construct(
            {"AGENT_LIVE_OUTPUT": "ture", "TELEGRAM_STREAM_DRAFTS": "0"}
        )
        self.assertFalse(reporter._live_enabled)
        self.assertFalse(reporter._stream_drafts)

    def test_flags_are_read_once_and_cached(self) -> None:
        from unittest.mock import AsyncMock

        with patch.object(runtime_flags, "enabled") as stub:
            stub.side_effect = [True, True]
            reporter = self._construct({})
        self.assertEqual(stub.call_count, 2)
        self.assertTrue(reporter._live_enabled)
        self.assertTrue(reporter._stream_drafts)
        reporter.bot = AsyncMock()
        reporter.progress.message_id = 3
        reporter._live_buffer = "معاينة"
        with patch.object(runtime_flags, "enabled") as reread:
            asyncio.run(reporter._publish_live(flush=True))
        reread.assert_not_called()
        reporter.bot.send_message_draft.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
