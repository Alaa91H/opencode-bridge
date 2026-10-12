"""Telegram callback integration for private host administration controls."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_telegram_control_ui import (
    FakeAgents,
    FakeDownloads,
    FakeModel,
    FakePreferences,
    FakeScheduleService,
    FakeSchedules,
    FakeTasks,
)

from bridge.services.admin_backups import AdminBackups
from bridge.services.host_credentials import HostCredentialManager
from bridge.services.runtime_controls import RuntimeControls
from bridge.services.worker_capacity import WorkerCapacity
from bridge.telegram.control_ui import ControlUI, callback


class AdminUIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        with sqlite3.connect(self.project / "sessions.db") as db:
            db.execute("CREATE TABLE checks (value TEXT)")
            db.execute("INSERT INTO checks VALUES ('ok')")
        self.host = HostCredentialManager(self.project)
        self.backups = AdminBackups(self.project, release_root=self.project / "releases")
        self.controls = RuntimeControls(self.project / "runtime")
        self.ui = ControlUI(
            preferences=FakePreferences(), agents=FakeAgents(), models=FakeModel(),
            tasks=FakeTasks(), schedule_store=FakeSchedules(),
            schedule_service=FakeScheduleService(), downloads=FakeDownloads(),
            capacity=WorkerCapacity(self.project / "workers"), wake=lambda: None,
            allowed=lambda _: True, admin_allowed=lambda _: True,
            host_credentials=self.host, backups=self.backups,
            runtime_controls=self.controls, project=self.project)
        self.query = SimpleNamespace(data="", answer=AsyncMock(),
                                     message=SimpleNamespace(chat_id=42, message_id=9))
        self.bot = SimpleNamespace(edit_message_text=AsyncMock(),
                                   send_document=AsyncMock())
        self.message = SimpleNamespace(text="", reply_text=AsyncMock(),
                                       delete=AsyncMock())
        self.status = SimpleNamespace(message_id=13, edit_text=AsyncMock())
        self.message.reply_text.return_value = self.status
        self.update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_user=SimpleNamespace(id=1), effective_message=self.message,
            callback_query=self.query, update_id=123,
            get_bot=lambda: self.bot)
        self.context = SimpleNamespace(user_data={}, bot=self.bot)

    async def press(self, text):
        self.query.data = text
        await self.ui.handle(self.update, self.context)

    async def test_named_token_wizard_activation_and_deletion(self):
        await self.press(callback("hostadd"))
        await self.press(callback("hostscope", "github"))
        self.message.text = "backup"
        await self.ui.on_text(self.update, self.context)
        self.message.text = "safe-test-token-123"
        await self.ui.on_text(self.update, self.context)
        self.assertEqual(self.host.catalog()[0]["name"], "backup")
        self.assertNotIn("safe-test-token-123", str(self.host.catalog()))
        await self.press(callback("hostactivate", 0))
        self.assertIn("safe-test-token-123", (self.project / ".env").read_text())
        await self.press(callback("confirm", "hostkey", 0))
        await self.press(callback("execute", "hostkey", 0))
        self.assertEqual(self.query.answer.await_count > 0, True)
        # Active credentials cannot be deleted until another is selected.
        self.assertTrue(self.host.catalog()[0]["active"])

    async def test_backup_create_send_delete(self):
        await self.press(callback("backupcreate"))
        items = self.backups.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["includes_keys"], False)
        await self.press(callback("backupsend", 0))
        self.bot.send_document.assert_awaited()
        await self.press(callback("confirm", "backup", 0))
        await self.press(callback("execute", "backup", 0))
        self.assertFalse(self.backups.list())

    async def test_system_update_auto_switch(self):
        await self.press(callback("toggleos"))
        self.assertFalse(self.controls.system_auto_update())
        await self.press(callback("togglebackuppurge"))
        self.assertTrue(self.controls.purge_backups_after_update())

    async def test_group_chat_cannot_access_credential_controls(self):
        self.update.effective_chat.type = "group"
        await self.press(callback("hostadd"))
        self.assertNotIn("ui_wizard", self.context.user_data)
        self.query.answer.assert_awaited()
