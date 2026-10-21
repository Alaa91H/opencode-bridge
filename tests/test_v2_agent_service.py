from __future__ import annotations

import unittest
from types import SimpleNamespace

import httpx

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
        self.send_calls = []
        self.send_errors = []

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

    async def send_prompt(
        self,
        session_id,
        prompt,
        *,
        model=None,
        agent=None,
        parts=None,
        variant=None,
    ):
        self.send_calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "model": model,
                "agent": agent,
                "parts": list(parts or []),
                "variant": variant,
            }
        )
        if self.send_errors:
            status = self.send_errors.pop(0)
            request = httpx.Request("POST", "https://example.invalid/prompt")
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError(
                f"status {status}",
                request=request,
                response=response,
            )
        return {"parts": [{"type": "text", "text": "ok"}]}


class FakeManager:
    preferred_model = "model/preferred"

    def __init__(self) -> None:
        self.fallback_model = None

    def variant_for_model(self, model):
        return "high" if model else None

    async def ensure_session_model(self, user_id, session_id, model, **kwargs):
        if kwargs.get("excluded_ids") and self.fallback_model:
            return self.fallback_model
        return model

    async def best_available_for_inputs(self, required_inputs):
        if required_inputs == {"image"}:
            return "model/media"
        return None


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


    async def test_current_model_and_input_routing_are_service_owned(self) -> None:
        session_id, model = await self.service.current_model("u")
        self.assertEqual(session_id, "s-1")
        self.assertEqual(model, "model/default")
        selected = await self.service.best_model_for_inputs(model, {"image"})
        self.assertEqual(selected, "model/media")

    async def test_send_prompt_uses_variant(self) -> None:
        response, model = await self.service.send_prompt_with_fallback(
            "u",
            "s-1",
            "hello",
            [],
            "model/default",
        )
        self.assertEqual(model, "model/default")
        self.assertEqual(response["parts"][0]["text"], "ok")
        self.assertEqual(self.client.send_calls[0]["variant"], "high")
        self.assertEqual(self.client.send_calls[0]["agent"], "development-agent")

    async def test_file_transport_failure_retries_without_direct_parts(self) -> None:
        self.client.send_errors = [415]
        response, _model = await self.service.send_prompt_with_fallback(
            "u",
            "s-1",
            "hello",
            [{"type": "file", "url": "file:///tmp/x"}],
            "model/default",
        )
        self.assertEqual(response["parts"][0]["text"], "ok")
        self.assertEqual(len(self.client.send_calls), 2)
        self.assertTrue(self.client.send_calls[0]["parts"])
        self.assertEqual(self.client.send_calls[1]["parts"], [])

    async def test_unavailable_model_falls_back_after_variant_retry(self) -> None:
        self.manager.fallback_model = "model/fallback"
        self.client.send_errors = [404, 404]
        _, model = await self.service.send_prompt_with_fallback(
            "u",
            "s-1",
            "hello",
            [],
            "model/default",
        )
        self.assertEqual(model, "model/fallback")
        self.assertEqual(len(self.client.send_calls), 3)
        self.assertEqual(self.client.send_calls[-1]["model"], "model/fallback")


if __name__ == "__main__":
    unittest.main()
