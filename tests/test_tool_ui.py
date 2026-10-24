"""Control-panel coverage for the owner's agent-tool manager."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.domain.policies.user_preferences import UserPreferences
from bridge.services.tool_manager import (
    TOOLS,
    ToolActionResult,
    ToolActivation,
    ToolAudit,
    ToolStatus,
)
from bridge.services.worker_capacity import WorkerCapacity
from bridge.telegram.control_ui import ControlUI, callback


class FakePreferences:
    def __init__(self):
        self.value = UserPreferences(language="ar")

    async def get(self, owner):
        return self.value


class FakeTools:
    def __init__(self):
        self.removed = []
        self.installed = []
        self.updated = []
        self.enabled = []
        self.statuses = {
            spec.key: ToolStatus(
                spec=spec,
                installed=spec.key in {"git", "gh", "curl", "jq"},
                package_installed=spec.key in {"git", "gh", "curl", "jq"},
                enabled=True,
                version="1.0" if spec.key == "jq" else "",
                paths=(),
                missing_commands=() if spec.key == "jq" else spec.commands,
                unsafe_paths=(),
                size_kib=128 if spec.key == "jq" else None,
            )
            for spec in TOOLS
        }
        self.busy = False

    async def status(self):
        return tuple(self.statuses.values())

    async def status_for(self, key):
        return self.statuses[key]

    def set_enabled(self, key, enabled):
        self.enabled.append((key, enabled))
        current = self.statuses[key]
        self.statuses[key] = ToolStatus(
            **{**current.__dict__, "enabled": enabled}
        )
        return self.statuses[key]

    def helper_available(self):
        return True

    async def install(self, key, *, progress=None):
        self.installed.append(key)
        return ToolActionResult(True, "installed")

    async def update(self, key, *, progress=None):
        self.updated.append(key)
        return ToolActionResult(True, "updated")

    async def remove(self, key, *, progress=None):
        self.removed.append(key)
        return ToolActionResult(True, "removed")

    async def activate_all(self, *, progress=None):
        return ToolActivation(
            True, ("jq",), ("jq",), (), ToolAudit(True, len(TOOLS), ()))

    async def harden(self):
        return ToolAudit(True, len(TOOLS), ())


class ToolUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = FakeTools()
        self.ui = ControlUI(
            preferences=FakePreferences(),
            agents=SimpleNamespace(),
            models=SimpleNamespace(),
            tasks=SimpleNamespace(),
            schedule_store=SimpleNamespace(),
            schedule_service=SimpleNamespace(),
            downloads=SimpleNamespace(),
            capacity=WorkerCapacity(self.root / "workers"),
            wake=lambda: None,
            allowed=lambda _: True,
            admin_allowed=lambda _: True,
            backups=None,
            project=self.root,
            tools=self.tools,
        )
        self.query = SimpleNamespace(data="", answer=AsyncMock(),
                                     message=SimpleNamespace(chat_id=42, message_id=9))
        self.message = SimpleNamespace(text="", reply_text=AsyncMock())
        self.status = SimpleNamespace(message_id=13, edit_text=AsyncMock())
        self.message.reply_text.return_value = self.status
        self.bot = SimpleNamespace(edit_message_text=AsyncMock())
        self.update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_user=SimpleNamespace(id=1),
            effective_message=self.message,
            callback_query=self.query,
            update_id=1,
            get_bot=lambda: self.bot,
        )
        self.context = SimpleNamespace(user_data={})

    async def press(self, data):
        self.query.data = data
        await self.ui.handle(self.update, self.context)

    async def test_tools_screen_lists_every_catalogue_entry(self):
        text, markup = await self.ui.render("1", "tools")
        self.assertIn("أدوات الوكيل", text)
        self.assertIn("Git", text)
        self.assertIn("Tesseract", text)
        flat = " ".join(
            button.text for row in markup.inline_keyboard for button in row
        )
        self.assertIn("تفعيل جميع الأدوات", flat)
        self.assertIn("فحص وتأمين", flat)

    async def test_tool_detail_and_toggle_update_policy_view(self):
        text, markup = await self.ui.render("1", "tools.jq")
        self.assertIn("1.0", text)
        self.assertIn("128", text)
        self.assertIn("تعطيل", " ".join(
            button.text for row in markup.inline_keyboard for button in row))
        await self.press(callback("tooltoggle", "jq"))
        self.assertEqual(self.tools.enabled, [("jq", False)])
        self.assertIn("معطّل", self.bot.edit_message_text.call_args.kwargs["text"])

    async def test_remove_is_confirmed_then_runs(self):
        await self.press(callback("confirm", "toolremove", "jq"))
        self.assertEqual(self.context.user_data.get("ui_confirm"), ("toolremove", "jq"))
        await self.press(callback("execute", "toolremove", "jq"))
        self.assertEqual(self.tools.removed, ["jq"])
        self.status.edit_text.assert_awaited()

    async def test_activate_all_and_harden_run_in_background_wrapper(self):
        await self.press(callback("toolharden"))
        self.assertTrue(self.status.edit_text.await_count >= 1)
        await self.press(callback("confirm", "toolactivateall", 0))
        await self.press(callback("execute", "toolactivateall", 0))
        self.assertTrue(self.status.edit_text.await_count >= 1)

    async def test_core_tool_has_no_toggle_or_remove_button(self):
        _text, markup = await self.ui.render("1", "tools.git")
        labels = " ".join(
            button.text for row in markup.inline_keyboard for button in row)
        self.assertNotIn("تعطيل", labels)
        self.assertNotIn("حذف", labels)
        self.assertIn("أساسية", _text)


if __name__ == "__main__":
    unittest.main()
