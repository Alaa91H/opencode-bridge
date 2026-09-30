"""End-to-end tests for the Telegram control panel.

These are not mocks. Every test drives the real router, the real SQLite
database created by the real migration runner, the real task/preference stores,
and — for anything that reaches OpenCode — a real HTTP server on a real socket
so the httpx client, auth, and JSON handling are genuinely exercised.

Only Telegram's own transport is substituted, with a recording bot, because the
Bot API cannot be called from a unit test.
"""

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.infrastructure.database.user_settings_store import UserSettingsStore
from bridge.services.download_service import DownloadService
from bridge.services.model_selection_service import ModelSelectionService
from bridge.services.user_preferences_service import UserPreferencesService
from bridge.telegram.panels import (
    MAX_CALLBACK_BYTES,
    MENU,
    PanelContext,
    PanelRouter,
    build_panels,
)
from opencode_client import OpenCodeClient
from session_store import SessionStore
from task_queue import TaskQueueStore

OWNER = "424242"


def _future(minutes: int = 60) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


def catalog_payload() -> dict[str, Any]:
    return {
        "all": [
            {
                "id": "opencode",
                "models": {
                    "alpha-free": {
                        "status": "active",
                        "cost": {"input": 0, "output": 0},
                        "capabilities": {"toolcall": True, "reasoning": True, "input": {"text": True}},
                        "limit": {"context": 1_000_000, "output": 64000},
                        "variants": {"max": {}, "xhigh": {}, "high": {}, "medium": {}, "low": {}},
                    },
                    "beta-free": {
                        "status": "active",
                        "cost": {"input": 0, "output": 0},
                        "capabilities": {"toolcall": True, "input": {"text": True}},
                        "limit": {"context": 100_000, "output": 16000},
                    },
                },
            }
        ]
    }


class _OpenCodeHandler(BaseHTTPRequestHandler):
    """A real HTTP endpoint that answers like the local OpenCode server."""

    def log_message(self, *args: Any) -> None:  # silence the default stderr logging
        return

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/global/health":
            self._send({"healthy": True, "version": "e2e-1.2.3"})
            return
        if self.path == "/provider":
            self._send(catalog_payload())
            return
        self._send({}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/session":
            self._send({"id": "ses_e2e"})
            return
        self._send({"ok": True})


class RecordingBot:
    """Stands in for the Telegram Bot and records what would have been sent."""

    def __init__(self) -> None:
        self.edits: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    async def edit_message_text(self, **kwargs: Any) -> "RecordingBot":
        self.edits.append(kwargs)
        return self

    async def send_message(self, **kwargs: Any) -> Any:
        self.messages.append(kwargs)
        return type("M", (), {"message_id": 1, **kwargs})()


class FakeQuery:
    def __init__(self, data: str | None, message: Any) -> None:
        self.data = data
        self.message = message
        self.answers: list[tuple[Any, bool]] = []

    async def answer(self, text: Any = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class FakeMessage:
    def __init__(self) -> None:
        self.chat_id = 10
        self.message_id = 20
        self.replies: list[dict[str, Any]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> Any:
        self.replies.append({"text": text, **kwargs})
        return self


class FakeUpdate:
    def __init__(self, data: str | None = None, owner: str = OWNER) -> None:
        self.effective_user = type("U", (), {"id": int(owner)})()
        self.effective_message = FakeMessage()
        self.callback_query = FakeQuery(data, self.effective_message) if data is not None else None
        self.bot = RecordingBot()

    def get_bot(self) -> RecordingBot:
        return self.bot


class PanelEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = BridgeDatabase(self.root / "bridge.db")
        await MigrationRunner(self.database).migrate()
        self.stores = SessionStore(self.root / "bridge.db")
        await self.stores.init()
        self.tasks = TaskQueueStore(self.root / "bridge.db")
        await self.tasks.init()
        self.preferences = UserPreferencesService(UserSettingsStore(self.database))
        self.server = HTTPServer(("127.0.0.1", 0), _OpenCodeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.client = OpenCodeClient(host="127.0.0.1", port=self.port, timeout=15.0)
        self.selection = ModelSelectionService(
            catalog_provider=self.client.list_providers,
            preferences=self.preferences,
            session_model_reader=self.stores.get_session,
            session_model_writer=self._write_session_model,
            variant_resolver=self._variant,
        )
        self.downloads = DownloadService(self.root / "downloads", allow_private_hosts=True)
        self.routers = self._build_routers()

    async def asyncTearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        await self.client.close()
        await self.stores.close()
        await self.tasks.close()
        await self.database.close()
        self.tmp.cleanup()

    async def _write_session_model(self, owner_id: str, session_id: str, model_id: str) -> None:
        await self.client.update_session(session_id, model=model_id)
        await self.stores.update_session(owner_id, model=model_id)

    async def _variant(self, owner_id: str, model_id: str) -> str | None:
        return None

    def _build_routers(self) -> dict[bool, PanelRouter]:
        providers: dict[str, Any] = {
            "health": self._health,
            "commands": self._commands,
            "limits": self._limits,
            "config": self._config,
            "preferences": self._preferences,
            "active": self._active,
            "failed": self._failed,
            "schedules": self._schedules,
            "downloads": self._downloads,
            "capabilities": self._capabilities,
        }

        def make(allowed: bool) -> PanelRouter:
            def context_for(owner_id: str) -> PanelContext:
                bound = {
                    key: (lambda owner=owner_id, value=value: value(owner)) for key, value in providers.items()
                }
                return PanelContext(owner_id, bound)

            return PanelRouter(
                panels=build_panels(),
                providers=context_for,
                actions={
                    ("tasks", "refresh"): self._noop,
                    ("tasks", "retry"): self._retry,
                    ("tasks", "cancel"): self._cancel,
                    ("schedules", "refresh"): self._noop,
                    ("schedules", "toggle_schedule"): self._toggle_schedule,
                    ("schedules", "delete_schedule"): self._delete_schedule,
                    ("system", "refresh"): self._noop,
                },
                is_allowed=lambda update: allowed,
            )

        return {True: make(True), False: make(False)}

    # ------------------------------------------------------------- providers

    async def _health(self, owner_id: str) -> Any:
        from bridge.services.health_service import ComponentHealth, HealthService, HealthState

        payload = await self.client.health()
        return HealthService().health(
            {
                "opencode": ComponentHealth(
                    "opencode",
                    HealthState.HEALTHY if payload.get("healthy") else HealthState.UNHEALTHY,
                    str(payload.get("version", "")),
                ),
                "telegram": ComponentHealth("telegram", HealthState.HEALTHY, "polling"),
            }
        )

    async def _commands(self, owner_id: str) -> tuple[str, ...]:
        del owner_id
        return ("help", "menu", "new", "model", "status", "tasks", "failed", "retry", "schedule", "schedules", "config")

    async def _limits(self, owner_id: str) -> dict[str, Any]:
        del owner_id
        return {"attachment_max_bytes": 20971520, "max_message_length": 4096}

    async def _config(self, owner_id: str) -> dict[str, Any]:
        del owner_id
        return {"model": "opencode/alpha-free"}

    async def _preferences(self, owner_id: str) -> Any:
        return await self.preferences.get(owner_id)

    async def _active(self, owner_id: str) -> list[Any]:
        return await self.tasks.list_active(owner_id)

    async def _failed(self, owner_id: str) -> list[Any]:
        return await self.tasks.list_failed(owner_id)

    async def _schedules(self, owner_id: str) -> list[Any]:
        return await self.tasks.list_scheduled_jobs(owner_id)

    async def _downloads(self, owner_id: str) -> list[Any]:
        return self.downloads.list(owner_id)

    async def _capabilities(self, owner_id: str) -> dict[str, Any]:
        del owner_id
        return self.downloads.capabilities()

    # --------------------------------------------------------------- actions

    async def _noop(self, context: PanelContext, arg: str) -> None:
        del arg
        return None

    async def _retry(self, context: PanelContext, arg: str) -> None:
        await self.tasks.retry_failed(int(arg), context.owner_id)

    async def _cancel(self, context: PanelContext, arg: str) -> None:
        await self.tasks.cancel(int(arg), context.owner_id)

    async def _toggle_schedule(self, context: PanelContext, arg: str) -> None:
        job = await self.tasks.get_scheduled_job_by_id(int(arg), context.owner_id)
        if job is not None:
            await self.tasks.set_scheduled_job_enabled(context.owner_id, job.name, not job.enabled)

    async def _delete_schedule(self, context: PanelContext, arg: str) -> None:
        job = await self.tasks.get_scheduled_job_by_id(int(arg), context.owner_id)
        if job is not None:
            await self.tasks.delete_scheduled_job(context.owner_id, job.name)

    # ----------------------------------------------------------------- tests

    async def test_menu_command_replies_with_every_section(self) -> None:
        update = FakeUpdate()
        await self.routers[True].show(update)
        self.assertEqual(len(update.effective_message.replies), 1)
        reply = update.effective_message.replies[0]
        labels = [b.text for row in reply["reply_markup"].inline_keyboard for b in row]
        for expected in ("الإعدادات", "المهام", "الجدولة", "حالة النظام", "المساعدة"):
            self.assertTrue(any(expected in label for label in labels), expected)
        self.assertNotIn("pnl:go:menu", [b.callback_data for row in reply["reply_markup"].inline_keyboard for b in row])

    async def test_every_screen_renders_and_carries_a_back_button(self) -> None:
        router = self.routers[True]
        for name in sorted(router.panels):
            with self.subTest(screen=name):
                text, keyboard = await router.render(name, OWNER)
                self.assertTrue(text.strip())
                payloads = [b.callback_data for row in keyboard.inline_keyboard for b in row]
                self.assertTrue(all(len(p.encode()) <= MAX_CALLBACK_BYTES for p in payloads))
                if name == MENU:
                    continue
                self.assertIn("pnl:go:menu", payloads)

    async def test_navigation_edits_the_existing_message(self) -> None:
        update = FakeUpdate(data="pnl:go:system")
        await self.routers[True].handle(update, None)
        self.assertEqual(len(update.bot.edits), 1)
        text = update.bot.edits[0]["text"]
        self.assertIn("opencode", text)
        self.assertIn("e2e-1.2.3", text)

    async def test_unknown_screen_is_reported_not_crashed(self) -> None:
        update = FakeUpdate(data="pnl:go:nope")
        await self.routers[True].handle(update, None)
        self.assertTrue(update.callback_query.answers[0][1])
        self.assertIn("unknown screen", str(update.callback_query.answers[0][0]))

    async def test_unauthorized_press_writes_nothing(self) -> None:
        update = FakeUpdate(data="pnl:go:system")
        await self.routers[False].handle(update, None)
        self.assertEqual(update.bot.edits, [])
        self.assertTrue(update.callback_query.answers[0][1])

    async def test_unauthorized_menu_command_is_refused(self) -> None:
        update = FakeUpdate()
        await self.routers[False].show(update)
        self.assertEqual(len(update.effective_message.replies), 1)
        self.assertIn("غير مصرح", update.effective_message.replies[0]["text"])

    async def test_tasks_screen_lists_a_real_failed_task(self) -> None:
        task, _sequence = await self.tasks.enqueue(OWNER, 10, "مهمة فاشلة", None)
        await self.tasks.finish(task.id, success=False, error="boom")
        text, keyboard = await self.routers[True].render("tasks", OWNER)
        self.assertIn("مهمة فاشلة", text)
        self.assertIn("boom", text)
        self.assertIn(f"pnl:do:tasks:retry:{task.id}", [
            b.callback_data for row in keyboard.inline_keyboard for b in row
        ])

    async def test_retry_flow_reaches_the_database(self) -> None:
        task, _sequence = await self.tasks.enqueue(OWNER, 10, "أعد المحاولة", None)
        await self.tasks.finish(task.id, success=False, error="boom")
        update = FakeUpdate(data=f"pnl:do:tasks:retry:{task.id}")
        await self.routers[True].handle(update, None)
        refreshed = await self.tasks.get(task.id)
        self.assertEqual(refreshed.status, "queued")

    async def test_destructive_action_requires_confirmation(self) -> None:
        job = await self.tasks.create_scheduled_job(OWNER, 10, "تقرير", "prompt", _future())
        update = FakeUpdate(data=f"pnl:do:schedules:delete_schedule:{job.id}")
        await self.routers[True].handle(update, None)
        self.assertIn("⚠️", update.bot.edits[0]["text"])
        self.assertIsNotNone(await self.tasks.get_scheduled_job(OWNER, "تقرير"))

        confirm = FakeUpdate(data=f"pnl:yes:schedules:delete_schedule:{job.id}")
        await self.routers[True].handle(confirm, None)
        self.assertIsNone(await self.tasks.get_scheduled_job(OWNER, "تقرير"))

    async def test_confirmation_can_be_cancelled(self) -> None:
        job = await self.tasks.create_scheduled_job(OWNER, 10, "تقرير", "prompt", _future())
        first = FakeUpdate(data=f"pnl:do:schedules:delete_schedule:{job.id}")
        await self.routers[True].handle(first, None)
        cancel = FakeUpdate(data="pnl:no:schedules")
        await self.routers[True].handle(cancel, None)
        self.assertIsNotNone(await self.tasks.get_scheduled_job(OWNER, "تقرير"))

    async def test_schedule_toggle_flips_real_state(self) -> None:
        job = await self.tasks.create_scheduled_job(OWNER, 10, "تقرير", "prompt", _future())
        self.assertTrue(job.enabled)
        await self.routers[True].handle(FakeUpdate(data=f"pnl:do:schedules:toggle_schedule:{job.id}"), None)
        self.assertFalse((await self.tasks.get_scheduled_job(OWNER, "تقرير")).enabled)

    async def test_stale_button_reports_instead_of_crashing(self) -> None:
        update = FakeUpdate(data="pnl:do:tasks:retry:999999")
        await self.routers[True].handle(update, None)
        self.assertTrue(update.callback_query.answers[0][1])

    async def test_settings_screen_reflects_a_saved_model_pin(self) -> None:
        await self.preferences.update(
            OWNER, model_preference="opencode/beta-free", model_variant="high", model_pinned=True
        )
        text, _ = await self.routers[True].render("settings", OWNER)
        self.assertIn("beta-free", text)
        self.assertIn("مفعّل", text)

    async def test_model_screen_opens_the_real_picker(self) -> None:
        from bridge.telegram.callbacks.model_picker import ModelPickerCallbackAdapter

        adapter = ModelPickerCallbackAdapter(
            service=self.selection,
            is_allowed=lambda update: True,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
        )
        update = FakeUpdate(data="pnl:go:model")
        await self.routers[True].handle(update, None)
        # The model panel button is a raw callback into the picker.
        payloads = [b.callback_data for row in update.bot.edits[0]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("mdl:open", payloads)

        from_panel = FakeUpdate(data="mdl:open")
        await adapter.handle(from_panel, None)
        labels = [b.text for row in from_panel.bot.edits[0]["reply_markup"].inline_keyboard for b in row]
        self.assertTrue(any("alpha" in label for label in labels))

    async def test_picker_reaches_the_live_catalog_and_persists_over_http(self) -> None:
        from bridge.telegram.callbacks.model_picker import ModelPickerCallbackAdapter

        adapter = ModelPickerCallbackAdapter(
            service=self.selection,
            is_allowed=lambda update: True,
            answer=lambda query, text=None, show_alert=False: query.answer(text, show_alert=show_alert),
        )
        opened = FakeUpdate(data="mdl:open")
        await adapter.handle(opened, None)
        payloads = [b.callback_data for row in opened.bot.edits[0]["reply_markup"].inline_keyboard for b in row]
        model_payload = next(p for p in payloads if p.startswith("mdl:m:"))

        picked = FakeUpdate(data=model_payload)
        await adapter.handle(picked, None)
        labels = [b.text for row in picked.bot.edits[0]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("Max — أقصى استدلال", labels[1])
        self.assertIn("Low — استدلال منخفض", labels[-2])

        stored = await self.preferences.get(OWNER)
        self.assertTrue(stored.model_pinned)
        self.assertEqual(stored.model_preference, "opencode/alpha-free")

    async def test_foreign_prefix_is_ignored(self) -> None:
        update = FakeUpdate(data="mdl:m:0")
        await self.routers[True].handle(update, None)
        self.assertEqual(update.bot.edits, [])
        self.assertEqual(update.callback_query.answers, [])

    async def test_help_screen_groups_real_commands(self) -> None:
        text, _ = await self.routers[True].render("help", OWNER)
        self.assertIn("/menu", text)
        self.assertIn("/model", text)
        self.assertIn("/retry", text)

    async def test_full_navigation_walk_from_menu_to_tasks_and_back(self) -> None:
        router = self.routers[True]
        task, _sequence = await self.tasks.enqueue(OWNER, 10, "مهمة", None)
        await self.tasks.finish(task.id, success=False, error="e")

        walk = ["pnl:go:tasks", "pnl:go:system", "pnl:go:settings", "pnl:go:menu"]
        for payload in walk:
            update = FakeUpdate(data=payload)
            await router.handle(update, None)
            self.assertEqual(len(update.bot.edits), 1, payload)
            self.assertTrue(update.bot.edits[0]["text"].strip())
            self.assertIsNotNone(update.bot.edits[0]["reply_markup"])
        self.assertEqual(len(router.panels["tasks"].needs), 2)
