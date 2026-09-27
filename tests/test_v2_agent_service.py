from __future__ import annotations

import unittest
from types import SimpleNamespace

from bridge.services.agent_service import AgentService, NoActiveSession


class FakeSessions:
    def __init__(self) -> None:
        self.items = {}

    async def get_session(self, user_id):
        return self.items.get(user_id)

    async def create_session(self, user_id, session_id, model=None):
        item = SimpleNamespace(
            telegram_user_id=user_id,
            opencode_session_id=session_id,
            model=model,
        )
        self.items[user_id] = item
        return item

    async def delete_session(self, user_id):
        return self.items.pop(user_id, None) is not None


class FakeClient:
    def __init__(self) -> None:
        self.created = 0
        self.aborted = []
        self.shared = []
        self.models = []

    async def create_session(self, title=None):
        self.created += 1
        return {"id": f"s-{self.created}"}

    async def update_session(self, session_id, *, model=None, title=None):
        return {"id": session_id, "model": model}

    async def abort_session(self, session_id):
        self.aborted.append(session_id)
        return True

    async def share_session(self, session_id):
        self.shared.append(session_id)
        return f"https://example.invalid/{session_id}"

    async def unshare_session(self, session_id):
        return True

    async def health(self):
        return {"healthy": True, "version": "test"}

    async def get_session_status(self):
        return {"s-1": {"state": "idle"}}

    async def list_agents(self):
        return [{"name": "agent", "mode": "primary"}]

    async def list_providers(self):
        return []


class FakeManager:
    preferred_model = "model/preferred"

    def variant_for_model(self, model):
        return "high" if model else None

    async def ensure_session_model(self, user_id, session_id, model, **kwargs):
        return model


class AgentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sessions = FakeSessions()
        self.client = FakeClient()
        self.manager = FakeManager()
        self.service = AgentService(
            self.client,
            self.sessions,
            default_model="model/default",
            default_agent="development-agent",
            default_variant="xhigh",
            variant_model="model/default",
            model_manager_provider=lambda: self.manager,
        )

    async def test_ensure_session_is_persistent_and_reused(self) -> None:
        first = await self.service.ensure_session("u")
        second = await self.service.ensure_session("u")
        self.assertEqual(first, "s-1")
        self.assertEqual(second, first)
        self.assertEqual(self.client.created, 1)
        self.assertEqual(self.sessions.items["u"].model, "model/default")

    async def test_fresh_session_aborts_and_replaces_existing(self) -> None:
        await self.service.ensure_session("u")
        replacement = await self.service.fresh_session("u")
        self.assertEqual(self.client.aborted, ["s-1"])
        self.assertEqual(replacement, "s-2")

    async def test_share_requires_active_session(self) -> None:
        with self.assertRaises(NoActiveSession):
            await self.service.share_current("missing")
        await self.service.ensure_session("u")
        self.assertEqual(
            await self.service.share_current("u"),
            "https://example.invalid/s-1",
        )

    async def test_status_is_service_owned_and_framework_independent(self) -> None:
        status = await self.service.status("u")
        self.assertFalse(status.has_session)
        await self.service.ensure_session("u")
        status = await self.service.status("u")
        self.assertTrue(status.has_session)
        self.assertTrue(status.healthy)
        self.assertEqual(status.agent, "development-agent")
        self.assertEqual(status.variant, "high")


if __name__ == "__main__":
    unittest.main()
