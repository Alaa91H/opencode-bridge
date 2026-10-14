"""Owner-only server controls in the Telegram panel: authorisation, confirmation, audit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bridge.services.host_control import SERVICES, ActionResult
from bridge.services.runtime_controls import RuntimeControls
from bridge.services.worker_capacity import WorkerCapacity
from bridge.telegram.control_ui import (
    WORDS,
    ControlUI,
    callback,
    is_admin_screen,
    pre_block,
)
from tests.test_telegram_control_ui import (
    FakeAgents,
    FakeDownloads,
    FakeModel,
    FakePreferences,
    FakeSchedules,
    FakeScheduleService,
    FakeTasks,
)

GIB = 1073741824


class FakeHost:
    def __init__(self):
        self.calls = []
        self.power = True
        self.logs = "line <b>1</b> & more"

    async def failed_units(self):
        return ["user:broken.service"]

    async def service_logs(self, key, lines=40):
        if key not in ("opencode", "bridge"):
            raise ValueError("unknown log source")
        self.calls.append(("logs", key))
        return self.logs

    async def top_processes(self, limit=8):
        return [{"pid": 42, "name": "opencode <x>", "rss": 512 * 1048576, "cpu": 12.5}]

    async def disk_breakdown(self):
        return {"items": [("runtime/downloads", 5 * 1048576, False), ("sessions.db", 4096, True)],
                "free": 10 * GIB, "total": 20 * GIB}

    async def power_available(self):
        return self.power

    async def restart_service(self, key):
        if key not in SERVICES:
            raise ValueError("unknown service key")
        self.calls.append(("restart", key))
        return ActionResult(True, "scheduled")

    async def cleanup_now(self):
        self.calls.append(("cleanup",))
        return ActionResult(True, "cleaned", "removed=2")

    async def reboot(self):
        self.calls.append(("reboot",))
        if not self.power:
            return ActionResult(False, "power_disabled")
        return ActionResult(True, "scheduled")


class HostPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = FakeHost()
        self.audit_events = []
        self.controls = RuntimeControls(self.root / "runtime")
        (self.root / "runtime").mkdir()
        self.ui = ControlUI(
            preferences=FakePreferences(), agents=FakeAgents(), models=FakeModel(),
            tasks=FakeTasks(), schedule_store=FakeSchedules(), schedule_service=FakeScheduleService(),
            downloads=FakeDownloads(), capacity=WorkerCapacity(self.root / "workers"),
            wake=lambda: None, allowed=lambda update: update.effective_user.id in (1, 2),
            admin_allowed=lambda update: update.effective_user.id == 1,
            runtime_controls=self.controls, project=self.root, host_control=self.host,
            audit=lambda *args, **kwargs: self.audit_events.append((args, kwargs)))
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace(send_document=AsyncMock()))
        self.bot = SimpleNamespace(edit_message_text=AsyncMock())
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.query = SimpleNamespace(data="", answer=AsyncMock(),
                                     message=SimpleNamespace(chat_id=1, message_id=25))
        self.update = self._update(user=1, chat_type="private")
        service_patch = patch(
            "bridge.services.service_monitor.service_overview",
            AsyncMock(return_value=[SimpleNamespace(unit="opencode-serve.service", status="active"),
                                    SimpleNamespace(unit="opencode-bridge-telegram.service",
                                                    status="failed")]))
        service_patch.start()
        self.addCleanup(service_patch.stop)

    def _update(self, *, user, chat_type):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user),
            effective_chat=SimpleNamespace(id=user, type=chat_type),
            effective_message=self.message, callback_query=self.query,
            get_bot=lambda: self.bot, update_id=99)

    async def press(self, data, update=None):
        self.query.data = data
        await self.ui.handle(update or self.update, self.context)

    def alerts(self):
        return [call for call in self.query.answer.await_args_list if call.kwargs.get("show_alert")]

    # ----------------------------------------------------------------- rendering
    async def test_every_new_screen_renders_with_safe_callbacks_in_both_languages(self):
        for lang in ("en", "ar"):
            self.ui.preferences.language = lang
            for screen in ("server", "svcctl", "procs", "disk", "power", "logs.opencode", "logs.bridge"):
                text, markup = await self.ui.render("1", screen)
                self.assertLess(len(text), 4096, screen)
                for row in markup.inline_keyboard:
                    for button in row:
                        self.assertLessEqual(len(button.callback_data.encode()), 64, screen)

    async def test_translations_cover_the_same_keys(self):
        self.assertEqual(set(WORDS["ar"]), set(WORDS["en"]))

    async def test_server_menu_links_to_every_new_tool(self):
        _text, markup = await self.ui.render("1", "server")
        targets = {button.callback_data for row in markup.inline_keyboard for button in row}
        for screen in ("svcctl", "procs", "disk", "power"):
            self.assertIn(callback("go", screen), targets)

    async def test_service_screen_lists_status_failed_units_and_confirmed_actions(self):
        text, markup = await self.ui.render("1", "svcctl")
        self.assertIn("opencode-bridge-telegram.service", text)
        self.assertIn("broken.service", text)
        targets = {button.callback_data for row in markup.inline_keyboard for button in row}
        for key in SERVICES:
            self.assertIn(callback("confirm", "svcrestart", key), targets)
        self.assertNotIn(callback("execute", "svcrestart", "opencode"), targets)  # never one-tap

    async def test_logs_are_html_escaped_and_always_fit_in_one_message(self):
        text, _ = await self.ui.render("1", "logs.opencode")
        self.assertIn("&lt;b&gt;1&lt;/b&gt; &amp; more", text)
        self.assertNotIn("<b>1</b>", text)
        self.host.logs = "<" * 5000 + "&" * 5000
        text, _ = await self.ui.render("1", "logs.bridge")
        self.assertLess(len(text), 4096)
        self.assertTrue(text.rstrip().endswith("</pre>"))

    async def test_unknown_log_source_is_not_a_screen(self):
        with self.assertRaises(ValueError):
            await self.ui.render("1", "logs.sshd")

    async def test_process_and_disk_tables_escape_names_and_show_numbers(self):
        procs, _ = await self.ui.render("1", "procs")
        self.assertIn("opencode &lt;x&gt;", procs)
        self.assertIn("512M", procs)
        self.assertIn("12.5%", procs)
        disk, markup = await self.ui.render("1", "disk")
        self.assertIn("runtime/downloads", disk)
        self.assertIn("free 10.00 / 20.00 GiB", disk)
        self.assertIn(callback("confirm", "cleanup", 0),
                      {button.callback_data for row in markup.inline_keyboard for button in row})

    async def test_reboot_button_exists_only_when_the_host_enabled_it(self):
        _text, markup = await self.ui.render("1", "power")
        self.assertIn(callback("confirm", "reboot", 0),
                      {button.callback_data for row in markup.inline_keyboard for button in row})
        self.host.power = False
        text, markup = await self.ui.render("1", "power")
        self.assertIn("OPENCODE_ENABLE_POWER_CONTROL=1", text)
        self.assertNotIn(callback("confirm", "reboot", 0),
                         {button.callback_data for row in markup.inline_keyboard for button in row})

    async def test_updates_screen_warns_when_root_assets_are_stale(self):
        text, _ = await self.ui.render("1", "updates")
        self.assertNotIn(WORDS["en"]["assets_stale"], text)
        (self.root / "runtime" / "root-assets-stale").write_text("2026-10-14T00:00:00Z\n")
        text, _ = await self.ui.render("1", "updates")
        self.assertIn(WORDS["en"]["assets_stale"], text)

    async def test_pre_block_never_exceeds_the_limit_after_escaping(self):
        for payload in ("<" * 9000, "&" * 9000, "plain\n" * 2000, ""):
            self.assertLessEqual(len(pre_block(payload)), 3400 + len("<pre></pre>"))

    # -------------------------------------------------------------- authorisation
    async def test_admin_screen_predicate_covers_every_new_screen(self):
        for screen in ("svcctl", "procs", "disk", "power", "logs.opencode", "advanced.tasks"):
            self.assertTrue(is_admin_screen(screen), screen)
        self.assertFalse(is_admin_screen("home"))

    async def test_non_owner_cannot_open_screens_or_trigger_actions(self):
        guest = self._update(user=2, chat_type="private")
        for data in (callback("go", "svcctl"), callback("go", "logs.opencode"),
                     callback("go", "procs"), callback("go", "disk"), callback("go", "power"),
                     callback("confirm", "svcrestart", "opencode"), callback("confirm", "cleanup", 0),
                     callback("confirm", "reboot", 0), callback("execute", "svcrestart", "opencode"),
                     callback("execute", "reboot", 0)):
            self.query.answer.reset_mock()
            await self.press(data, guest)
            self.assertTrue(self.alerts(), data)
        self.assertEqual(self.host.calls, [])
        self.bot.edit_message_text.assert_not_awaited()

    async def test_owner_in_a_group_chat_is_refused(self):
        group = self._update(user=1, chat_type="supergroup")
        for data in (callback("go", "svcctl"), callback("confirm", "reboot", 0),
                     callback("execute", "svcrestart", "bridge")):
            self.query.answer.reset_mock()
            await self.press(data, group)
            self.assertTrue(self.alerts(), data)
        self.assertEqual(self.host.calls, [])

    async def test_show_ignores_non_owner_and_non_private_requests(self):
        for update in (self._update(user=2, chat_type="private"), self._update(user=1, chat_type="group")):
            self.message.reply_text.reset_mock()
            await self.ui.show(update, self.context, "svcctl")
            self.message.reply_text.assert_not_awaited()
        await self.ui.show(self.update, self.context, "svcctl")
        self.message.reply_text.assert_awaited_once()

    # ------------------------------------------------------ confirmation + audit
    async def test_execute_without_a_prior_confirmation_does_nothing(self):
        for data in (callback("execute", "svcrestart", "opencode"), callback("execute", "cleanup", 0),
                     callback("execute", "reboot", 0)):
            await self.press(data)
        self.assertEqual(self.host.calls, [])
        self.assertEqual(len(self.alerts()), 3)

    async def test_confirmation_is_bound_to_the_exact_action_and_target(self):
        await self.press(callback("confirm", "svcrestart", "opencode"))
        await self.press(callback("execute", "svcrestart", "stack"))  # different target
        self.assertEqual(self.host.calls, [])

    async def test_confirmed_restart_runs_once_replies_and_is_audited(self):
        await self.press(callback("confirm", "svcrestart", "opencode"))
        await self.press(callback("execute", "svcrestart", "opencode"))
        self.assertEqual(self.host.calls, [("restart", "opencode")])
        self.message.reply_text.assert_awaited_with(WORDS["en"]["host_scheduled"])
        (args, kwargs), = self.audit_events
        self.assertEqual(args, ("host_control", "ok"))
        self.assertEqual(kwargs["actor_id"], 1)
        self.assertEqual(kwargs["details"], {"action": "svcrestart", "target": "opencode"})
        # A second tap cannot replay the same confirmation.
        await self.press(callback("execute", "svcrestart", "opencode"))
        self.assertEqual(self.host.calls, [("restart", "opencode")])

    async def test_hostile_service_key_is_rejected_by_the_allow_list(self):
        for key in ("../../etc", "opencode-serve.service; reboot", "sshd"):
            await self.press(callback("confirm", "svcrestart", key))
            await self.press(callback("execute", "svcrestart", key))
        self.assertEqual(self.host.calls, [])

    async def test_confirmed_cleanup_reports_detail(self):
        await self.press(callback("confirm", "cleanup", 0))
        await self.press(callback("execute", "cleanup", 0))
        self.assertEqual(self.host.calls, [("cleanup",)])
        self.message.reply_text.assert_awaited_with(
            WORDS["en"]["host_cleaned"].format(detail="removed=2"))

    async def test_reboot_requires_confirmation_and_reports_when_disabled(self):
        self.host.power = False
        await self.press(callback("confirm", "reboot", 0))
        await self.press(callback("execute", "reboot", 0))
        self.message.reply_text.assert_awaited_with(WORDS["en"]["host_power_disabled"])
        (args, _kwargs), = self.audit_events
        self.assertEqual(args, ("host_control", "power_disabled"))

    async def test_audit_failure_never_blocks_the_operation(self):
        def broken(*_a, **_k):
            raise OSError("disk full")

        self.ui._audit_sink = broken
        await self.press(callback("confirm", "svcrestart", "bridge"))
        await self.press(callback("execute", "svcrestart", "bridge"))
        self.assertEqual(self.host.calls, [("restart", "bridge")])

    async def test_result_is_never_rendered_with_unformatted_braces(self):
        for key, text in WORDS["en"].items():
            if key.startswith("host_"):
                text.format(detail="x")
        for key, text in WORDS["ar"].items():
            if key.startswith("host_"):
                text.format(detail="x")


if __name__ == "__main__":
    unittest.main()
