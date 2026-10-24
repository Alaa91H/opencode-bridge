"""Tests for the /tools command adapter."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.services.tool_manager import TOOLS, ToolManagerError, ToolStatus
from bridge.telegram.commands.tools import ToolCommands


class FakeTools:
    def __init__(self):
        self.busy = False
        self.statuses = {
            spec.key: ToolStatus(
                spec=spec,
                installed=spec.key in {"git", "gh", "curl", "jq"},
                package_installed=spec.key in {"git", "gh", "curl", "jq"},
                enabled=True,
                version="1.0" if spec.key == "jq" else "",
                paths=(),
                missing_commands=() if spec.key in {"git", "gh", "curl", "jq"} else spec.commands,
                unsafe_paths=(),
                size_kib=128 if spec.key == "jq" else None,
            )
            for spec in TOOLS
        }

    async def status(self):
        return tuple(self.statuses.values())

    async def status_for(self, key):
        try:
            return self.statuses[key]
        except KeyError as exc:
            raise ToolManagerError("unknown tool") from exc


class ToolCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = AsyncMock()
        self.commands = ToolCommands(
            FakeTools(), reply=self.reply, error_message=lambda exc, action: "error")
        self.update = SimpleNamespace(
            effective_message=SimpleNamespace(),
            effective_user=SimpleNamespace(id=1),
        )

    async def test_overview_lists_states_and_keys(self):
        await self.commands.tools(self.update, SimpleNamespace(args=[]))
        text = self.reply.await_args.args[1]
        self.assertIn("أدوات الوكيل", text)
        self.assertIn("Git", text)
        self.assertIn("Tesseract", text)
        self.assertIn("/tools", text)

    async def test_detail_shows_version_size_and_command(self):
        await self.commands.tools(self.update, SimpleNamespace(args=["jq"]))
        text = self.reply.await_args.args[1]
        self.assertIn("jq", text)
        self.assertIn("1.0", text)
        self.assertIn("128 KiB", text)

    async def test_unknown_key_is_reported_without_crashing(self):
        await self.commands.tools(self.update, SimpleNamespace(args=["nope"]))
        self.assertIn("غير معروفة", self.reply.await_args.args[1])


if __name__ == "__main__":
    unittest.main()
