from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN_FOR_IMPORT_ONLY")
os.environ.setdefault("TELEGRAM_ALLOWED_USERS", "1")

import bot


class BotImportTests(unittest.TestCase):
    def test_default_agent_is_operator(self) -> None:
        self.assertEqual(bot.DEFAULT_AGENT, "telegram-operator")

    def test_help_keeps_commands_and_excludes_removed_operational_text(self) -> None:
        self.assertIn("/health", bot.HELP_TEXT)
        self.assertNotIn("المرفقات: فيك تبعت", bot.HELP_TEXT)
        self.assertNotIn("أثناء التنفيذ، البوت بيحدّث", bot.HELP_TEXT)
        self.assertNotIn("أوامر البناء والتجميع", bot.HELP_TEXT)

    def test_research_commands_are_registered(self) -> None:
        expected = {"search", "deepresearch", "extreme", "news", "compare", "factcheck", "verify", "open", "extract"}
        self.assertTrue(expected.issubset(bot.RESEARCH_COMMAND_MODES))
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        self.assertIn("async def cmd_research_mode", source)
        self.assertIn("execution_mode=mode.value", source)
        self.assertIn("CommandHandler(tuple(RESEARCH_COMMAND_MODES), cmd_research_mode)", source)
        self.assertIn("/deepresearch", bot.HELP_TEXT)
        self.assertIn("/factcheck", bot.HELP_TEXT)

    def test_start_command_does_not_append_help_text(self) -> None:
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        start_block = source[source.index("async def cmd_start"):source.index("async def cmd_new")]
        self.assertNotIn("HELP_TEXT", start_block)
        self.assertIn("startup_message()", start_block)

    def test_task_ui_uses_one_message_without_user_visible_ids(self) -> None:
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn("رقم_المهمة", bot.HELP_TEXT)
        self.assertNotIn("مهمة اليوم رقم", source)
        self.assertNotIn("بترتيب {position}", source)
        self.assertNotIn("#{task.id}", source)
        self.assertNotIn("_task_reply_chunks", source)
        self.assertIn("status_message_id=status_message_id", source)
        self.assertIn("reporter.finalize_text(", source)

    def test_reboot_decision_buttons_are_registered(self) -> None:
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        self.assertIn("async def handle_reboot_callback", source)
        self.assertIn('pattern=r"^reboot:(now|cancel)$"', source)
        self.assertIn("REBOOT_DECISION_PATH", source)


if __name__ == "__main__":
    unittest.main()
