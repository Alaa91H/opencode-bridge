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
        task_adapter = (PROJECT_DIR / "bridge" / "telegram" / "commands" / "tasks.py").read_text(encoding="utf-8")
        task_service = (PROJECT_DIR / "bridge" / "services" / "task_service.py").read_text(encoding="utf-8")
        self.assertIn("async def cmd_research_mode", source)
        self.assertIn("_task_command_adapter().research(update, context)", source)
        self.assertIn("execution_mode=mode", task_adapter)
        self.assertIn('"execution_mode": execution_mode.value', task_service)
        self.assertIn("idempotency_scope", task_service)
        app_source = (PROJECT_DIR / "bridge" / "telegram" / "app.py").read_text(encoding="utf-8")
        self.assertIn("CommandHandler(research_command_names, handlers.cmd_research_mode)", app_source)
        self.assertIn("/deepresearch", bot.HELP_TEXT)
        self.assertIn("/factcheck", bot.HELP_TEXT)

    def test_start_command_does_not_append_help_text(self) -> None:
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        start_block = source[source.index("async def cmd_start"):source.index("async def cmd_new")]
        agent_adapter = (PROJECT_DIR / "bridge" / "telegram" / "commands" / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn("HELP_TEXT", start_block)
        self.assertIn("_agent_command_adapter().start(update, context)", start_block)
        self.assertIn("self.startup_text()", agent_adapter)

    def test_task_ui_uses_one_message_without_user_visible_ids(self) -> None:
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn("رقم_المهمة", bot.HELP_TEXT)
        self.assertNotIn("مهمة اليوم رقم", source)
        self.assertNotIn("بترتيب {position}", source)
        self.assertNotIn("#{task.id}", source)
        self.assertNotIn("_task_reply_chunks", source)
        task_adapter = (PROJECT_DIR / "bridge" / "telegram" / "commands" / "tasks.py").read_text(encoding="utf-8")
        self.assertIn("status_message_id=status_message_id", task_adapter)
        execution_service = (PROJECT_DIR / "bridge" / "services" / "task_execution_service.py").read_text(encoding="utf-8")
        self.assertIn("reporter.finalize_text(", execution_service)

    def test_reboot_decision_buttons_are_registered(self) -> None:
        source = (PROJECT_DIR / "bot.py").read_text(encoding="utf-8")
        app_source = (PROJECT_DIR / "bridge" / "telegram" / "app.py").read_text(encoding="utf-8")
        callback_source = (PROJECT_DIR / "bridge" / "telegram" / "callbacks" / "reboot.py").read_text(encoding="utf-8")
        self.assertIn("async def handle_reboot_callback", source)
        self.assertIn('pattern=r"^reboot:(now|cancel)$"', app_source)
        self.assertIn("decision_path", callback_source)


if __name__ == "__main__":
    unittest.main()
