from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
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
        now = datetime.now(timezone.utc)
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


if __name__ == "__main__":
    unittest.main()
