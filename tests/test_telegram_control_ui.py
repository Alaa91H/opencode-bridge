"""Behavioral button tests: bilingual screens, authenticated callbacks, real service wiring and capacity."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.domain.policies.user_preferences import UserPreferences
from bridge.services.worker_capacity import WorkerCapacity
from bridge.telegram.control_ui import ControlUI, callback


class FakePreferences:
    def __init__(self, lang="en"):
        self.values = {"1": UserPreferences(language=lang)}

    async def get(self, owner):
        return self.values.get(owner, UserPreferences())

    async def update(self, owner, **changes):
        from dataclasses import replace
        self.values[owner] = replace(await self.get(owner), **changes)
        return self.values[owner]


class FakeModel:
    def __init__(self):
        self.choices = [
            SimpleNamespace(model_id="free/demo", label="Demo", variants=("xhigh", "high", "low"))
        ]
        self.current_model = "free/demo"
        self.current_variant = None
        self.calls = []

    async def view(self, owner):
        return SimpleNamespace(
            choices=self.choices, current_model=self.current_model,
            current_variant=self.current_variant,
            model_at=lambda i: self.choices[i] if i == 0 else None)

    async def select_model(self, owner, index):
        self.calls.append(("model", owner, index))
        return await self.view(owner)

    async def select_variant(self, owner, model, variant):
        self.calls.append(("variant", owner, model, variant))
        self.current_variant = None if variant == 0 else self.choices[model].variants[variant - 1]
        return await self.view(owner)

    async def auto(self, owner):
        self.calls.append(("auto", owner))
        return await self.view(owner)


class FakeAgents:
    default_agent = "development-agent"

    async def agents(self):
        return [{"name": "development-agent"}, {"name": "build"}]

    async def fresh_session(self, owner):
        return None


class FakeTasks:
    def __init__(self):
        self.enqueued = []
        self.cancelled = []
        self.retried = []
        self.deleted = []
        self.priorities = []
    async def active(self, owner):
        return [SimpleNamespace(id=8, status="running", prompt="Running")]
    async def failed(self, owner):
        return [SimpleNamespace(id=9, status="failed", prompt="Failed")]
    async def recent(self, owner):
        return []

    async def change_priority(self, owner, task_id, delta):
        self.priorities.append((owner, task_id, delta))
        return SimpleNamespace(id=task_id)

    async def remove(self, owner, task_id):
        self.deleted.append((owner, task_id))
        return SimpleNamespace(id=task_id)

    async def enqueue_prompt(self, *args, **kwargs):
        self.enqueued.append((args, kwargs))
        return SimpleNamespace(task=SimpleNamespace(id=42))
    async def cancel(self, task_id, owner):
        self.cancelled.append((task_id, owner))
        return SimpleNamespace(id=task_id)
    async def retry_failed(self, owner, task_id):
        self.retried.append((owner, task_id))
        return SimpleNamespace(id=task_id)


class FakeSchedules:
    def __init__(self):
        self.created = []
    async def list_scheduled_jobs(self, owner):
        return [SimpleNamespace(id=11, name="Daily scan", enabled=True, next_run_at=None, timezone_name="UTC")]
    async def get_scheduled_job_by_id(self, i, owner):
        return SimpleNamespace(id=i, name="Daily scan", enabled=True, next_run_at=None, timezone_name="UTC") if i == 11 else None
    async def set_scheduled_job_enabled(self, owner, name, enabled):
        self.created.append(("toggle", name, enabled))
    async def delete_scheduled_job(self, owner, name):
        self.created.append(("delete", name))


class FakeScheduleService:
    def __init__(self):
        self.created = []
    async def create_once(self, owner, chat, name, prompt, when, **kwargs):
        self.created.append(("once", owner, name, prompt, when))
        return SimpleNamespace(id=51)
    async def create_recurring(self, owner, chat, name, prompt, interval, **kwargs):
        self.created.append(("recurring", owner, name, prompt, interval))
        return SimpleNamespace(id=52)


class FakeDownloads:
    def list(self, owner):
        return []


class UIFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.capacity = WorkerCapacity(Path(self.temp.name) / "workers")
        self.prefs = FakePreferences()
        self.tasks = FakeTasks()
        self.models = FakeModel()
        self.schedules = FakeSchedules()
        self.scheduler = FakeScheduleService()
        self.wakes = []
        self.ui = ControlUI(
            preferences=self.prefs, agents=FakeAgents(), models=self.models,
            tasks=self.tasks, schedule_store=self.schedules,
            schedule_service=self.scheduler, downloads=FakeDownloads(),
            capacity=self.capacity, wake=lambda: self.wakes.append(True),
            allowed=lambda update: update.effective_user.id == 1)
        self.context = SimpleNamespace(user_data={}, bot=SimpleNamespace(
            send_document=AsyncMock()))
        self.bot = SimpleNamespace(edit_message_text=AsyncMock())
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.status = SimpleNamespace(message_id=9, edit_text=AsyncMock())
        self.message.reply_text.return_value = self.status
        self.query = SimpleNamespace(
            data="", answer=AsyncMock(),
            message=SimpleNamespace(chat_id=1, message_id=25))
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1), effective_message=self.message,
            callback_query=self.query, get_bot=lambda: self.bot, update_id=99)

    async def press(self, data):
        self.query.data = data
        await self.ui.handle(self.update, self.context)

    async def test_bilingual_full_menu_and_keyboard(self):
        en, markup = await self.ui.render("1", "home")
        self.assertIn("OpenCode Bridge", en)
        self.assertTrue(all(len(b.callback_data.encode("utf-8")) <= 64
                            for row in markup.inline_keyboard for b in row))
        keyboard = self.ui.keyboard("ar")
        self.assertIn("إدارة الوكيل", str(keyboard.keyboard))
        await self.press(callback("lang", "ar"))
        self.assertEqual((await self.prefs.get("1")).language, "ar")
        ar, _ = await self.ui.render("1", "home")
        self.assertIn("لوحة تحكم", ar)
        self.assertTrue(self.message.reply_text.await_count > 0)

    async def test_live_agent_selection_is_saved(self):
        _text, markup = await self.ui.render("1", "agents")
        self.assertIn("build", str(markup.inline_keyboard))
        await self.press(callback("agent", 1))
        self.assertEqual((await self.prefs.get("1")).agent_preference, "build")
        await self.press(callback("agent", "auto"))
        self.assertIsNone((await self.prefs.get("1")).agent_preference)

    async def test_model_then_reasoning_are_real_actions(self):
        await self.press(callback("model", 0))
        self.assertEqual(self.models.calls[-1], ("model", "1", 0))
        await self.press(callback("reason", 0, 2))
        self.assertEqual(self.models.calls[-1], ("variant", "1", 0, 2))
        self.assertEqual(self.models.current_variant, "high")

    async def test_direct_and_daily_input_preserve_original_prompt(self):
        await self.press(callback("input", "daily"))
        self.message.text = "Complex instructions\nstep 1"
        await self.ui.on_text(self.update, self.context)
        self.assertEqual(self.tasks.enqueued[-1][0][2], self.message.text)
        self.assertTrue(self.tasks.enqueued[-1][1]["daily_continuation"])
        self.assertTrue(self.tasks.enqueued[-1][1]["direct_dispatch"])
        self.assertTrue(self.wakes)
        await self.press(callback("input", "direct"))
        self.message.text = "Do this once"
        await self.ui.on_text(self.update, self.context)
        self.assertFalse(self.tasks.enqueued[-1][1]["daily_continuation"])

    async def test_one_or_two_only_and_persistence(self):
        self.assertEqual(self.capacity.get(), 1)
        await self.press(callback("workers", 2))
        self.assertEqual(WorkerCapacity(self.capacity.path).get(), 2)
        await self.press(callback("workers", 1))
        self.assertEqual(self.capacity.get(), 1)
        with self.assertRaises(ValueError):
            self.capacity.set(3)

    async def test_cancel_requires_confirmation_and_owner_check(self):
        await self.press(callback("confirm", "cancel", 8))
        self.assertFalse(self.tasks.cancelled)
        await self.press(callback("execute", "cancel", 8))
        self.assertEqual(self.tasks.cancelled, [(8, "1")])
        self.update.effective_user.id = 2
        await self.press(callback("workers", 2))
        self.assertEqual(self.capacity.get(), 1)
        self.query.answer.assert_awaited()

    async def test_schedule_wizard_and_toggle(self):
        await self.press(callback("input", "schedule_recurring"))
        self.assertEqual(self.context.user_data["ui_wizard"]["step"], "name")
        for value in ("monitor", "1d", "Review the repository"):
            self.message.text = value
            await self.ui.on_text(self.update, self.context)
        self.assertEqual(self.scheduler.created[0][:4],
                         ("recurring", "1", "monitor", "Review the repository"))
        await self.press(callback("schedule", 11))
        self.assertIn(("toggle", "Daily scan", False), self.schedules.created)

    async def test_priority_and_deletion_buttons(self):
        await self.press(callback("priority", 8, 1))
        self.assertEqual(self.tasks.priorities, [("1", 8, 1)])
        await self.press(callback("confirm", "task", 8))
        self.assertFalse(self.tasks.deleted)
        await self.press(callback("execute", "task", 8))
        self.assertEqual(self.tasks.deleted, [("1", 8)])

    async def test_selected_country_changes_schedule_time(self):
        from datetime import UTC, datetime
        await self.press(callback("tz", 0))
        self.assertEqual((await self.prefs.get("1")).timezone, "Europe/Berlin")
        await self.press(callback("input", "schedule_once"))
        for value in ("Meeting", "2026-12-01 12:00", "Reminder"):
            self.message.text = value
            await self.ui.on_text(self.update, self.context)
        self.assertEqual(self.scheduler.created[-1][-1],
                         datetime(2026, 12, 1, 11, tzinfo=UTC))

    async def test_invalid_callback_and_input_are_nonfatal(self):
        await self.press(callback("agent", 999))
        self.query.answer.assert_awaited()
        self.assertIsNone((await self.prefs.get("1")).agent_preference)
        self.assertFalse(await self.ui.on_text(self.update, self.context))


    async def test_single_server_and_credentials_button_in_both_languages(self):
        from bridge.telegram.control_ui import BINDINGS, BUTTON_KEYS, WORDS

        self.assertEqual(sum(k == "server" for row in BUTTON_KEYS for k in row), 1)
        self.assertEqual(sum(k == "credentials" for row in BUTTON_KEYS for k in row), 1)
        self.assertNotIn("keys", [entry for row in BUTTON_KEYS for entry in row])
        self.assertNotIn("hostkeys", [entry for row in BUTTON_KEYS for entry in row])
        for language in ("ar", "en"):
            with self.subTest(language=language):
                labels = [button.text for row in self.ui.keyboard(language).keyboard
                          for button in row]
                self.assertEqual(len(labels), len(set(labels)), "Reply keyboard must not repeat labels")
                self.assertEqual(labels.count(WORDS[language]["server"]), 1)
                self.assertEqual(labels.count(WORDS[language]["credentials"]), 1)
                self.assertEqual(BINDINGS[WORDS[language]["keys"]], "keys",
                                 "Existing keyboard shortcuts must keep working")
                self.assertEqual(BINDINGS[WORDS[language]["hostkeys"]], "hostkeys")
                self.assertEqual(BINDINGS[WORDS[language]["credentials"]], "credentials")

    async def test_credentials_navigation_and_nested_back_button(self):
        from bridge.telegram.control_ui import WORDS

        for language in ("ar", "en"):
            self.prefs.values["1"] = UserPreferences(language=language)
            _, home = await self.ui.render("1", "home")
            self.assertIn(callback("go", "credentials"),
                          [b.callback_data for row in home.inline_keyboard for b in row])
            self.assertNotIn(callback("go", "keys"),
                             [b.callback_data for row in home.inline_keyboard for b in row])
            text, submenu = await self.ui.render("1", "credentials")
            self.assertIn(WORDS[language]["credentials_text"], text)
            options = [b.callback_data for row in submenu.inline_keyboard for b in row]
            self.assertIn(callback("go", "keys"), options)
            self.assertIn(callback("go", "hostkeys"), options)
            self.assertIn(callback("go", "home"), options)
            for screen in ("keys", "hostkeys"):
                _, detail = await self.ui.render("1", screen)
                self.assertEqual(detail.inline_keyboard[-1][0].callback_data,
                                 callback("go", "credentials"))

    async def test_credentials_callback_rejected_in_non_private_chat(self):
        self.update.effective_chat.type = "group"
        await self.press(callback("go", "credentials"))
        self.query.answer.assert_awaited()
        self.assertEqual(self.bot.edit_message_text.await_count, 0)


    async def test_update_mode_buttons_drive_persistent_policy(self):
        from bridge.services.runtime_controls import RuntimeControls
        from bridge.telegram.control_ui import WORDS

        controls = RuntimeControls(Path(self.temp.name) / "auto-controls")
        self.ui.runtime_controls = controls
        self.update.effective_chat.type = "private"
        text, markup = await self.ui.render("1", "updates")
        self.assertIn(WORDS["en"]["mode_safe"], text)
        choices = [b.callback_data for row in markup.inline_keyboard for b in row]
        for mode in ("off", "notify", "auto-safe", "maintenance-window"):
            self.assertIn(callback("autopilot", mode), choices)
            await self.press(callback("autopilot", mode))
            self.assertEqual(RuntimeControls(controls.root).autopilot_mode(), mode)
        await self.press(callback("autopilot", "invalid"))
        self.assertEqual(controls.autopilot_mode(), "maintenance-window")

        self.update.effective_chat.type = "group"
        await self.press(callback("autopilot", "off"))
        self.assertEqual(controls.autopilot_mode(), "maintenance-window",
                         "Autopilot state must be private-owner only")


    async def test_bridge_upgrade_is_explicitly_confirmed_and_owner_only(self):
        from bridge.services.runtime_controls import RuntimeControls

        self.ui.runtime_controls = RuntimeControls(Path(self.temp.name) / "auto-controls")
        _text, markup = await self.ui.render("1", "updates")
        keys = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertIn(callback("checkbridge"), keys)
        self.assertIn(callback("confirm", "updatebridge", 0), keys)

        self.update.effective_chat.type = "private"
        await self.press(callback("confirm", "updatebridge", 0))
        self.assertEqual(self.context.user_data["ui_confirm"], ("updatebridge", "0"))
        self.update.effective_chat.type = "group"
        await self.press(callback("execute", "updatebridge", 0))
        self.assertEqual(self.message.reply_text.await_count, 0)

    async def test_server_submenu_contains_services_without_extra_root_buttons(self):
        from unittest.mock import patch

        from bridge.services.service_monitor import ServiceState

        text, markup = await self.ui.render("1", "server")
        self.assertTrue(text)
        buttons = [item.callback_data for row in markup.inline_keyboard for item in row]
        self.assertIn(callback("go", "services"), buttons)
        self.assertEqual(buttons.count(callback("go", "services")), 1)
        fake = [ServiceState("opencode-serve.service", "active"),
                ServiceState("opencode-bridge-telegram.service", "failed")]
        with patch("bridge.services.service_monitor.service_overview", new=AsyncMock(return_value=fake)):
            message, detail = await self.ui.render("1", "services")
        self.assertIn("opencode-serve.service", message)
        self.assertIn("failed", message)
        self.assertIn(callback("go", "services"),
                      [item.callback_data for row in detail.inline_keyboard for item in row])

        self.update.effective_chat.type = "group"
        await self.press(callback("go", "services"))
        self.query.answer.assert_awaited()
        self.assertEqual(self.bot.edit_message_text.await_count, 0)



if __name__ == "__main__":
    unittest.main()
