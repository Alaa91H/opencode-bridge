from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from model_manager import ModelManager


@dataclass
class UserSession:
    telegram_user_id: str
    opencode_session_id: str
    created_at: datetime
    updated_at: datetime
    model: str | None = None


def provider_catalog() -> dict:
    return {
        "all": [
            {
                "id": "opencode",
                "models": {
                    "text-only": {
                        "status": "active",
                        "cost": {"input": 0, "output": 0},
                        "capabilities": {"input": {"text": True}, "toolcall": True, "reasoning": True},
                        "limit": {"context": 1_000_000, "output": 128_000},
                    },
                    "general-rich": {
                        "status": "active",
                        "cost": {"input": 0, "output": 0},
                        "capabilities": {
                            "attachment": True,
                            "toolcall": True,
                            "reasoning": True,
                            "input": {"text": True, "image": True, "pdf": True},
                        },
                        "limit": {"context": 200_000, "output": 64_000},
                        "variants": {"low": {}, "high": {}, "xhigh": {}},
                    },
                },
            }
        ]
    }


class FakeClient:
    def __init__(self, states: dict | None = None) -> None:
        self.states = states or {}
        self.updated: list[tuple[str, str]] = []

    async def list_providers(self) -> dict:
        return provider_catalog()

    async def get_session_status(self) -> dict:
        return self.states

    async def update_session(self, session_id: str, model: str) -> dict:
        self.updated.append((session_id, model))
        return {"id": session_id, "model": model}


class FakeStore:
    def __init__(self, sessions: list[UserSession] | None = None) -> None:
        self.sessions = sessions or []
        self.updated: list[tuple[str, str]] = []

    async def update_session(self, telegram_user_id: str, model: str) -> None:
        self.updated.append((telegram_user_id, model))
        for session in self.sessions:
            if session.telegram_user_id == telegram_user_id:
                session.model = model

    async def list_sessions(self) -> list[UserSession]:
        return self.sessions


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def write(self, event: str, outcome: str, actor_id: str | None = None, details: dict | None = None) -> None:
        self.events.append((event, outcome, details or {}))


class CountingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def list_providers(self) -> dict:
        self.calls += 1
        return await super().list_providers()


class CatalogCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_task_reuses_fresh_catalog(self) -> None:
        client = CountingClient()
        manager = ModelManager(client, FakeStore(), FakeAudit(), fallback_model="opencode/legacy")
        await manager.ensure_session_model("user-1", "session-1", "opencode/text-only")
        await manager.ensure_session_model("user-1", "session-1", "opencode/text-only")
        self.assertEqual(client.calls, 1)

    async def test_stale_catalog_is_refetched(self) -> None:
        client = CountingClient()
        manager = ModelManager(client, FakeStore(), FakeAudit(), fallback_model="opencode/legacy")
        await manager.ensure_session_model("user-1", "session-1", "opencode/text-only")
        # White-box: an age far beyond any sync interval. 0.0 is not reliably
        # stale because time.monotonic() can start near zero on a fresh VM.
        manager._providers_at = -1.0e9
        await manager.ensure_session_model("user-1", "session-1", "opencode/text-only")
        self.assertEqual(client.calls, 2)

    def test_public_cache_api_roundtrip_without_fetch(self) -> None:
        client = CountingClient()
        manager = ModelManager(client, FakeStore(), FakeAudit(), fallback_model="opencode/legacy")
        self.assertIsNone(manager.cached_providers())
        payload = {"all": []}
        manager.cache_providers(payload)
        self.assertEqual(manager.cached_providers(), payload)
        self.assertEqual(client.calls, 0)


class ModelManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_session_switches_to_best_general_model(self) -> None:
        client = FakeClient()
        store = FakeStore()
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        selected = await manager.ensure_session_model("user-1", "session-1", "opencode/text-only")
        self.assertEqual(selected, "opencode/general-rich")
        self.assertEqual(client.updated, [("session-1", "opencode/general-rich")])
        self.assertEqual(store.updated, [("user-1", "opencode/general-rich")])

    async def test_pinned_default_wins_when_available(self) -> None:
        client = FakeClient()
        store = FakeStore()
        audit = FakeAudit()
        manager = ModelManager(
            client,
            store,
            audit,
            fallback_model="opencode/text-only",
            pin_default_model=True,
        )
        selected = await manager.best_available()
        self.assertEqual(selected, "opencode/text-only")

    async def test_media_selection_overrides_text_only_pin_only_for_capability(self) -> None:
        client = FakeClient()
        store = FakeStore()
        audit = FakeAudit()
        manager = ModelManager(
            client,
            store,
            audit,
            fallback_model="opencode/text-only",
            pin_default_model=True,
        )
        selected = await manager.best_available_for_inputs({"image"})
        self.assertEqual(selected, "opencode/general-rich")
        self.assertEqual(manager.configured_model, "opencode/text-only")

    async def test_best_available_caches_maximum_variant(self) -> None:
        client = FakeClient()
        store = FakeStore()
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        selected = await manager.best_available()
        self.assertEqual(selected, "opencode/general-rich")
        self.assertEqual(manager.variant_for_model(selected), "xhigh")

    async def test_ensure_session_can_exclude_failed_model(self) -> None:
        client = FakeClient()
        store = FakeStore()
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        selected = await manager.ensure_session_model(
            "user-1",
            "session-1",
            "opencode/general-rich",
            excluded_ids={"opencode/general-rich"},
        )
        self.assertEqual(selected, "opencode/text-only")
        self.assertEqual(client.updated, [("session-1", "opencode/text-only")])

    async def test_reconcile_skips_busy_session_and_updates_idle_session(self) -> None:
        now = datetime.now(UTC)
        idle = UserSession("idle-user", "idle-session", now, now, model="opencode/text-only")
        busy = UserSession("busy-user", "busy-session", now, now, model="opencode/text-only")
        client = FakeClient({"busy-session": {"state": "running"}, "idle-session": {"state": "idle"}})
        store = FakeStore([idle, busy])
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        selected = await manager.reconcile_once()
        self.assertEqual(selected, "opencode/general-rich")
        self.assertEqual(client.updated, [("idle-session", "opencode/general-rich")])
        self.assertEqual(store.updated, [("idle-user", "opencode/general-rich")])

    async def test_owner_pin_wins_over_catalog_best_for_that_owner_only(self) -> None:
        client = FakeClient()
        store = FakeStore()
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        manager.set_owner_pin_reader(_pin_reader({"user-1": "opencode/text-only"}))
        self.assertEqual(
            await manager.ensure_session_model("user-1", "session-1", "opencode/general-rich"),
            "opencode/text-only",
        )
        self.assertEqual(
            await manager.ensure_session_model("user-2", "session-2", "opencode/text-only"),
            "opencode/general-rich",
        )

    async def test_reconcile_never_overrides_an_owner_pin(self) -> None:
        now = datetime.now(UTC)
        pinned = UserSession("pinned-user", "pinned-session", now, now, model="opencode/text-only")
        free = UserSession("free-user", "free-session", now, now, model="opencode/text-only")
        client = FakeClient({"pinned-session": {"state": "idle"}, "free-session": {"state": "idle"}})
        store = FakeStore([pinned, free])
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        manager.set_owner_pin_reader(_pin_reader({"pinned-user": "opencode/text-only"}))
        selected = await manager.reconcile_once()
        self.assertEqual(selected, "opencode/general-rich")
        self.assertEqual(client.updated, [("free-session", "opencode/general-rich")])
        self.assertNotIn("pinned-user", [owner for owner, _ in store.updated])

    async def test_daily_scout_keeps_owner_pins_and_reports_two_counts(self) -> None:
        now = datetime.now(UTC)
        pinned = UserSession("pinned-user", "pinned-session", now, now, model="opencode/text-only")
        free = UserSession("free-user", "free-session", now, now, model="opencode/legacy")
        client = FakeClient()
        store = FakeStore([pinned, free])
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/legacy")
        manager.set_owner_pin_reader(_pin_reader({"pinned-user": "opencode/text-only"}))
        changed, failed = await manager.force_all_sessions("opencode/general-rich")
        self.assertEqual((changed, failed), (1, 0))
        self.assertEqual(pinned.model, "opencode/text-only")
        self.assertEqual(free.model, "opencode/general-rich")

    async def test_resolve_variant_prefers_owner_level_then_catalog(self) -> None:
        client = FakeClient()
        manager = ModelManager(client, FakeStore(), FakeAudit(), fallback_model="opencode/legacy")
        await manager.best_available()
        self.assertEqual(await manager.resolve_variant("user-1", "opencode/general-rich"), "xhigh")
        manager.set_owner_variant_reader(
            _variant_reader({"user-1": {"opencode/general-rich": "low"}})
        )
        self.assertEqual(await manager.resolve_variant("user-1", "opencode/general-rich"), "low")
        self.assertEqual(await manager.resolve_variant("user-2", "opencode/general-rich"), "xhigh")

    async def test_owner_pin_reader_failure_falls_back_to_automatic(self) -> None:
        client = FakeClient()
        manager = ModelManager(client, FakeStore(), FakeAudit(), fallback_model="opencode/legacy")

        async def broken(owner_id: str) -> str:
            raise RuntimeError("database gone")

        manager.set_owner_pin_reader(broken)
        self.assertIsNone(await manager.owner_pinned_model("user-1"))
        self.assertEqual(await manager.best_available(owner_id="user-1"), "opencode/general-rich")


def _pin_reader(pins: dict[str, str]):
    async def read(owner_id: str) -> str | None:
        return pins.get(owner_id)

    return read


def _variant_reader(levels: dict[str, dict[str, str]]):
    async def read(owner_id: str, model_id: str) -> str | None:
        return levels.get(owner_id, {}).get(model_id)

    return read


if __name__ == "__main__":
    unittest.main()
