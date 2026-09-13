from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import model_manager as model_manager_module
from agent_scout import parse_research_model, primary_agent_ids, select_primary_agent
from model_manager import ModelManager


@dataclass
class UserSession:
    telegram_user_id: str
    opencode_session_id: str
    created_at: datetime
    updated_at: datetime
    model: str | None = None


def catalog() -> dict:
    return {
        "all": [
            {
                "id": "opencode",
                "models": {
                    "basic-free": {
                        "status": "active",
                        "cost": {"input": 0, "output": 0},
                        "capabilities": {"toolcall": True, "reasoning": True, "input": {"text": True}},
                        "limit": {"context": 100_000, "output": 16_000},
                    },
                    "rich-free": {
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
                    "paid-pro": {
                        "status": "active",
                        "cost": {"input": 1, "output": 2},
                        "capabilities": {"toolcall": True, "reasoning": True, "input": {"text": True}},
                        "limit": {"context": 1_000_000, "output": 128_000},
                    },
                },
            }
        ]
    }


class FakeClient:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []

    async def list_agents(self):
        return [
            {"name": "explore", "mode": "subagent"},
            {"name": "plan", "mode": "primary"},
            {"name": "development-agent", "mode": "primary"},
        ]

    async def list_providers(self):
        return catalog()

    async def update_session(self, session_id: str, model: str):
        self.updated.append((session_id, model))
        return {"id": session_id, "model": model}


class FakeStore:
    def __init__(self, sessions: list[UserSession]) -> None:
        self.sessions = sessions
        self.updated: list[tuple[str, str]] = []

    async def list_sessions(self):
        return self.sessions

    async def update_session(self, telegram_user_id: str, model: str):
        self.updated.append((telegram_user_id, model))
        for session in self.sessions:
            if session.telegram_user_id == telegram_user_id:
                session.model = model


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def write(self, event: str, outcome: str, actor_id=None, details=None) -> None:
        self.events.append((event, outcome, details or {}))


class AgentScoutPolicyTests(unittest.TestCase):
    def test_only_primary_agents_are_candidates(self) -> None:
        agents = [
            {"name": "build", "mode": "primary"},
            {"name": "general", "mode": "subagent"},
            {"name": "hidden-primary", "mode": "primary", "hidden": True},
        ]
        self.assertEqual(primary_agent_ids(agents), ["build"])

    def test_project_development_agent_is_preferred(self) -> None:
        agents = [
            {"name": "build", "mode": "primary"},
            {"name": "development-agent", "mode": "primary"},
        ]
        self.assertEqual(select_primary_agent(agents), "development-agent")

    def test_research_parser_rejects_model_outside_allowlist(self) -> None:
        text = '{"model":"opencode/paid-pro","reason":"benchmark"}'
        self.assertIsNone(parse_research_model(text, ["opencode/basic-free", "opencode/rich-free"]))


class DailyScoutRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_scout_applies_best_free_choice_to_all_saved_sessions(self) -> None:
        now = datetime.now(timezone.utc)
        sessions = [
            UserSession("u1", "s1", now, now, "opencode/basic-free"),
            UserSession("u2", "s2", now, now, "opencode/basic-free"),
        ]
        client = FakeClient()
        store = FakeStore(sessions)
        audit = FakeAudit()
        manager = ModelManager(client, store, audit, fallback_model="opencode/basic-free")
        manager.scout_web_research = False

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "agent-scout.json"
            with patch.object(model_manager_module, "SCOUT_STATE_PATH", state_path):
                result = await manager.scout_once()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["selected_agent"], "development-agent")
        self.assertEqual(result["selected_model"], "opencode/rich-free")
        self.assertEqual(manager.preferred_agent, "development-agent")
        self.assertEqual(manager.preferred_model, "opencode/rich-free")
        self.assertEqual(
            client.updated,
            [("s1", "opencode/rich-free"), ("s2", "opencode/rich-free")],
        )
        self.assertEqual(result["sessions_changed"], 2)

    async def test_paid_preference_can_never_override_live_free_catalog(self) -> None:
        manager = ModelManager(FakeClient(), FakeStore([]), FakeAudit(), fallback_model="opencode/basic-free")
        manager.set_preferred_model("opencode/paid-pro")
        self.assertEqual(await manager.best_available(), "opencode/rich-free")


if __name__ == "__main__":
    unittest.main()
