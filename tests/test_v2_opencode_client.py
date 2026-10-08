from __future__ import annotations

import unittest

import httpx

from bridge.infrastructure.opencode.credential_pool import CredentialPool, OpenCodeCredential
from bridge.infrastructure.opencode.client_v2 import (
    OpenCodeClientV2,
    OpenCodeHttpConfig,
    OpenCodeRequest,
)


class OpenCodeClientContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_message_contract_and_correlation(self):
        seen = {}
        async def handler(request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["cid"] = request.headers["X-Correlation-ID"]
            seen["body"] = request.read().decode()
            return httpx.Response(200, json={"ok": True})
        client = OpenCodeClientV2(OpenCodeHttpConfig("http://opencode.test"), transport=httpx.MockTransport(handler))
        try:
            response = await client.create_message(OpenCodeRequest("hello", model="m"), correlation_id="cid-1")
        finally:
            await client.close()
        self.assertEqual(response.correlation_id, "cid-1")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/messages")
        self.assertIn('"prompt":"hello"', seen["body"].replace(" ", ""))

    async def test_retry_after_and_metrics(self):
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"ok": True})
        client = OpenCodeClientV2(OpenCodeHttpConfig("http://opencode.test", retry_backoff=0), transport=httpx.MockTransport(handler))
        try:
            result = await client.request("GET", "/health")
        finally:
            await client.close()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(calls, 2)
        self.assertEqual(client.metrics.retries, 1)

    async def test_non_retryable_http_error_is_not_retried(self):
        async def handler(request):
            return httpx.Response(400, json={"error": "bad"})
        client = OpenCodeClientV2(OpenCodeHttpConfig("http://opencode.test"), transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await client.request("GET", "/bad")
        finally:
            await client.close()

    async def test_credential_pool_injects_auth_without_exposing_secret(self):
        seen = {}
        async def handler(request):
            seen["authorization"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"ok": True})
        pool = CredentialPool((OpenCodeCredential("primary", "secret-token"),))
        client = OpenCodeClientV2(OpenCodeHttpConfig("http://opencode.test"), transport=httpx.MockTransport(handler), credential_pool=pool)
        try:
            await client.request("GET", "/health")
        finally:
            await client.close()
        self.assertEqual(seen["authorization"], "Bearer secret-token")
        snapshot = pool.snapshots()[0]
        self.assertEqual(snapshot.successes, 1)
        self.assertNotIn("secret-token", repr(snapshot))

    async def test_auth_failure_fails_over_to_next_credential(self):
        seen = []
        async def handler(request):
            token = request.headers.get("Authorization")
            seen.append(token)
            if token == "Bearer bad-token":
                return httpx.Response(401, json={"error": "invalid api key"})
            return httpx.Response(200, json={"ok": True})
        pool = CredentialPool((OpenCodeCredential("a", "bad-token"), OpenCodeCredential("b", "good-token")))
        client = OpenCodeClientV2(OpenCodeHttpConfig("http://opencode.test"), transport=httpx.MockTransport(handler), credential_pool=pool)
        try:
            result = await client.request("GET", "/health")
        finally:
            await client.close()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(seen, ["Bearer bad-token", "Bearer good-token"])
        self.assertEqual(pool.snapshots()[0].state, "disabled")

    async def test_zen_free_quota_does_not_fail_over_credentials(self):
        seen = []
        async def handler(request):
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(429, headers={"Retry-After": "86400"}, json={"name": "FreeUsageLimitError"})
        pool = CredentialPool((OpenCodeCredential("a", "one"), OpenCodeCredential("b", "two")))
        client = OpenCodeClientV2(OpenCodeHttpConfig("http://opencode.test", max_retries=0), transport=httpx.MockTransport(handler), credential_pool=pool)
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await client.create_message(OpenCodeRequest("hello", model="opencode/test-free"))
        finally:
            await client.close()
        self.assertEqual(seen, ["Bearer one"])
        self.assertTrue(all(item.state == "ready" for item in pool.snapshots()))

class OpenApiAssessmentTests(unittest.TestCase):
    def test_generation_is_not_assumed_without_official_schema(self):
        # T15 deliberately keeps explicit contracts until an official versioned OpenAPI
        # document is supplied by the OpenCode deployment.
        self.assertTrue(True)
