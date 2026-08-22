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

    def test_help_is_arabic_and_explains_build_policy(self) -> None:
        self.assertIn("أوامر البناء", bot.HELP_TEXT)
        self.assertIn("/health", bot.HELP_TEXT)


if __name__ == "__main__":
    unittest.main()
