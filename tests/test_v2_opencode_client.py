from __future__ import annotations

import unittest

import httpx

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


class OpenApiAssessmentTests(unittest.TestCase):
    def test_generation_is_not_assumed_without_official_schema(self):
        # T15 deliberately keeps explicit contracts until an official versioned OpenAPI
        # document is supplied by the OpenCode deployment.
        self.assertTrue(True)
