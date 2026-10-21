"""Contract: every Telegram command stays reachable from all surfaces.

Guards three registries against drift without importing the live bot:
1. every BotCommand in core_commands() has a handler registration,
2. every legacy dispatch allowlist entry has a cmd_ handler,
3. recent management-center commands stay wired into the control UI.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "bridge" / "telegram" / "app.py").read_text(encoding="utf-8")
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
CONTROL_UI_SOURCE = (ROOT / "bridge" / "telegram" / "control_ui.py").read_text(encoding="utf-8")

RESEARCH_MODES = {
    "search", "deepresearch", "extreme", "news", "compare",
    "factcheck", "verify", "open", "extract",
}


def _core_commands() -> list[str]:
    return re.findall(r'BotCommand\("([a-z]+)"', APP_SOURCE)


def _legacy_allowlist() -> list[str]:
    match = re.search(r"if name not in frozenset\(\[(.*?)\]\)", BOT_SOURCE, re.DOTALL)
    assert match is not None, "legacy dispatch allowlist not found"
    return re.findall(r'"([a-z]+)"', match.group(1))


class CommandCoverageTests(unittest.TestCase):
    def test_every_core_command_has_a_handler(self) -> None:
        names = _core_commands()
        self.assertGreater(len(names), 40)
        for name in names:
            with self.subTest(command=name):
                if name in RESEARCH_MODES:
                    self.assertIn("CommandHandler(research_command_names", APP_SOURCE)
                else:
                    self.assertIn(f'CommandHandler("{name}"', APP_SOURCE)

    def test_every_legacy_allowlist_entry_has_a_handler(self) -> None:
        names = _legacy_allowlist()
        self.assertGreater(len(names), 30)
        for name in names:
            with self.subTest(command=name):
                self.assertIn(f"async def cmd_{name}", BOT_SOURCE)

    def test_management_center_commands_stay_in_control_ui(self) -> None:
        for name in ("mirror", "prompt", "topics", "taskshow", "dailies", "exec", "metrics"):
            with self.subTest(command=name):
                self.assertIn(f'"{name}"', CONTROL_UI_SOURCE)


if __name__ == "__main__":
    unittest.main()
