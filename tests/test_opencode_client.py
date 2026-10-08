from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from bridge.infrastructure.opencode.credential_pool import CredentialPool, OpenCodeCredential\nfrom opencode_client import OpenCodeClient, message_model_reference


class OpenCodeClientPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_prompt_forwards_xhigh_variant(self) -> None:
        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"parts": []})

        client = OpenCodeClient()
        await client.close()
        client._client = httpx.AsyncClient(
            base_url="http://127.0.0.1:4096",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.send_prompt(
                "ses_test",
                "Improve the project",
                model="opencode/muse-spark-1.3-contributor-free",
                agent="development-agent",
                variant="xhigh",
            )
        finally:
            await client.close()

        self.assertEqual(captured["variant"], "xhigh")
        self.assertEqual(captured["model"], {"providerID": "opencode", "modelID": "muse-spark-1.3-contributor-free"})
        self.assertEqual(captured["agent"], "development-agent")

    async def test_production_client_fails_over_invalid_credential(self) -> None:
        seen = []
        async def handler(request: httpx.Request) -> httpx.Response:
            token = request.headers.get("Authorization")
            seen.append(token)
            if token == "Bearer bad":
                return httpx.Response(401, json={"error": "invalid api key"})
            return httpx.Response(200, json={"parts": []})
        pool = CredentialPool((OpenCodeCredential("a", "bad"), OpenCodeCredential("b", "good")))
        client = OpenCodeClient(credential_pool=pool)
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://agent", transport=httpx.MockTransport(handler))
        try:
            await client.send_prompt("session", "work", model="provider/model")
        finally:
            await client.close()
        self.assertEqual(seen, ["Bearer bad", "Bearer good"])
        self.assertEqual(pool.snapshots()[0].state, "disabled")

    async def test_production_client_does_not_rotate_zen_free_ip_quota(self) -> None:
        seen = []
        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(429, headers={"Retry-After": "86400"}, json={"name": "FreeUsageLimitError"})
        pool = CredentialPool((OpenCodeCredential("a", "one"), OpenCodeCredential("b", "two")))
        client = OpenCodeClient(credential_pool=pool)
        await client._client.aclose()
        client._client = httpx.AsyncClient(base_url="http://agent", transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await client.send_prompt("session", "work", model="opencode/test-free")
        finally:
            await client.close()
        self.assertEqual(seen, ["Bearer one"])
        self.assertTrue(all(item.state == "ready" for item in pool.snapshots()))

class OpenCodeClientModelTests(unittest.TestCase):
    def test_message_model_reference_converts_persisted_model_id(self) -> None:
        self.assertEqual(
            message_model_reference("opencode/muse-spark-1.2-contributor-free"),
            {"providerID": "opencode", "modelID": "muse-spark-1.2-contributor-free"},
        )

    def test_message_model_reference_accepts_valid_object(self) -> None:
        self.assertEqual(
            message_model_reference({"providerID": "opencode", "modelID": "muse-spark-1.2-contributor-free"}),
            {"providerID": "opencode", "modelID": "muse-spark-1.2-contributor-free"},
        )

    def test_message_model_reference_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            message_model_reference("not-a-qualified-model")


if __name__ == "__main__":
    unittest.main()
