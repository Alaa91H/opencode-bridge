import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bridge.domain.tasks.retry_policy import ProviderTaskError, TaskCancelledError, classify_retry
from opencode_client import OpenCodeClient


class ResumableOpenCodeClientTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, handler):
        client = OpenCodeClient()
        await client.close()
        client._client = httpx.AsyncClient(base_url="http://agent", transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.close)
        return client

    async def test_new_message_is_submitted_asynchronously_and_result_is_matched(self):
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append((request.url.path, json.loads(request.content)))
                return httpx.Response(204)
            if request.url.path.endswith("/message/msg_saved"):
                return httpx.Response(404)
            if request.url.path == "/session/status":
                return httpx.Response(200, json={"saved": {"type": "idle"}})
            return httpx.Response(200, json=[
                {"info": {"role": "assistant", "parentID": "other", "time": {"created": 20, "completed": 21}}, "parts": []},
                {"info": {"role": "assistant", "parentID": "msg_saved", "time": {"created": 1, "completed": 2}},
                 "parts": [{"type": "text", "text": "completed work"}]},
            ])

        client = await self.client(handler)
        result = await client.send_prompt("saved", "work", model="provider/model", message_id="msg_saved")
        self.assertEqual(posts[0][0], "/session/saved/prompt_async")
        self.assertEqual(posts[0][1]["messageID"], "msg_saved")
        self.assertEqual(result["parts"][0]["text"], "completed work")

    async def test_restart_reconnects_to_existing_message_without_post(self):
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request)
            if request.url.path.endswith("/message/msg_saved"):
                return httpx.Response(200, json={"info": {"role": "user", "id": "msg_saved"}})
            if request.url.path == "/session/status":
                return httpx.Response(200, json={})
            return httpx.Response(200, json=[{"info": {"role": "assistant", "parentID": "msg_saved", "time": {"completed": 2}},
                                            "parts": [{"type": "text", "text": "saved result"}]}])

        client = await self.client(handler)
        result = await client.send_prompt("saved", "resume", message_id="msg_saved")
        self.assertEqual(posts, [])
        self.assertEqual(result["parts"][0]["text"], "saved result")

    async def test_embedded_provider_error_is_never_treated_as_completed(self):
        def handler(request):
            if request.url.path.endswith("/message/msg_saved"):
                return httpx.Response(200, json={})
            if request.url.path == "/session/status":
                return httpx.Response(200, json={})
            return httpx.Response(200, json=[{"info": {"role": "assistant", "parentID": "msg_saved",
                                                      "error": {"data": {"statusCode": 429}}}, "parts": []}])

        client = await self.client(handler)
        with self.assertRaises(ProviderTaskError) as captured:
            await client.send_prompt("saved", "work", message_id="msg_saved")
        self.assertTrue(classify_retry(captured.exception).unlimited)

    async def test_provider_retry_state_defers_without_resubmitting(self):
        def handler(request):
            if request.url.path.endswith("/message/msg_saved"):
                return httpx.Response(200, json={})
            if request.url.path == "/session/status":
                return httpx.Response(200, json={"saved": {"type": "retry", "message": "rate limit reached"}})
            return httpx.Response(200, json=[])

        client = await self.client(handler)
        with self.assertRaises(ProviderTaskError) as captured:
            await client.send_prompt("saved", "work", message_id="msg_saved")
        decision = classify_retry(captured.exception)
        self.assertTrue(decision.pending)
        self.assertTrue(decision.unlimited)

    async def test_outer_wall_deadline_is_classified_as_retryable_transport(self):
        class Deadline:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                raise TimeoutError("wall deadline")

        client = await self.client(lambda _request: httpx.Response(200, json={}))
        with patch("opencode_client.asyncio.timeout", return_value=Deadline()):
            with self.assertRaises(httpx.ReadTimeout) as captured:
                await client._task_request("GET", "/session/status")
        self.assertTrue(classify_retry(captured.exception).pending)

    async def test_cancel_before_submission_never_posts(self):
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request.url.path)
            return httpx.Response(404)

        client = await self.client(handler)
        with self.assertRaises(TaskCancelledError):
            await client.send_prompt("saved", "work", message_id="msg_saved", should_continue=AsyncMock(return_value=False))
        self.assertEqual(posts, [])

    async def test_cancel_after_submission_aborts_before_polling(self):
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request.url.path)
                return httpx.Response(204)
            return httpx.Response(404)

        client = await self.client(handler)
        with self.assertRaises(TaskCancelledError):
            await client.send_prompt("saved", "work", message_id="msg_saved", should_continue=AsyncMock(side_effect=[True, False]))
        self.assertEqual(posts, ["/session/saved/prompt_async", "/session/saved/abort"])

    async def test_idle_interruption_allows_continuation_instead_of_reconnecting_forever(self):
        for reply in ([], [{"info": {"role": "assistant", "parentID": "msg_saved", "time": {"created": 1}}, "parts": []}]):
            with self.subTest(reply=reply):
                def handler(request, reply=reply):
                    if request.url.path.endswith("/message/msg_saved"):
                        return httpx.Response(200, json={})
                    if request.url.path == "/session/status":
                        return httpx.Response(200, json={"saved": {"type": "idle"}})
                    return httpx.Response(200, json=reply)

                client = await self.client(handler)
                with patch("opencode_client.asyncio.sleep", new_callable=AsyncMock):
                    with self.assertRaises(ProviderTaskError) as captured:
                        await client.send_prompt("saved", "work", message_id="msg_saved")
                self.assertFalse(classify_retry(captured.exception).pending)
