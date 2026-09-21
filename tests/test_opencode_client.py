from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from free_points import FreePointsTracker
from opencode_client import OpenCodeClient, message_model_reference


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


class OpenCodeClientUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_prompt_counts_new_assistant_turns_as_points(self) -> None:
        get_calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal get_calls
            if request.method == "GET" and request.url.path == "/session/ses_test/message":
                get_calls += 1
                if get_calls == 1:
                    messages = [
                        {"info": {"id": "msg_old", "role": "assistant"}, "parts": []},
                    ]
                else:
                    messages = [
                        {"info": {"id": "msg_old", "role": "assistant"}, "parts": []},
                        {"info": {"id": "msg_tool_1", "role": "assistant"}, "parts": []},
                        {"info": {"id": "msg_tool_2", "role": "assistant"}, "parts": []},
                        {"info": {"id": "msg_final", "role": "assistant"}, "parts": []},
                    ]
                return httpx.Response(200, json=messages)
            if request.method == "POST" and request.url.path == "/session/ses_test/message":
                return httpx.Response(200, json={"parts": [{"type": "text", "text": "done"}]})
            return httpx.Response(404)

        with tempfile.TemporaryDirectory() as tmp:
            tracker = FreePointsTracker(Path(tmp) / "points.db", daily_limit=200, timezone_name="UTC")
            client = OpenCodeClient(free_points_tracker=tracker)
            await client.close()
            client._client = httpx.AsyncClient(
                base_url="http://127.0.0.1:4096",
                transport=httpx.MockTransport(handler),
            )
            try:
                response = await client.send_prompt("ses_test", "Do work")
            finally:
                await client.close()

            self.assertEqual(response["_bridge_usage_points"], 3)
            self.assertEqual(tracker.snapshot().used, 3)
            self.assertEqual(tracker.snapshot().remaining, 197)

    async def test_free_limit_error_marks_local_estimate_exhausted(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(
                429,
                json={"type": "FreeUsageLimitError", "message": "Free usage exceeded"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            tracker = FreePointsTracker(Path(tmp) / "points.db", daily_limit=200, timezone_name="UTC")
            client = OpenCodeClient(free_points_tracker=tracker)
            await client.close()
            client._client = httpx.AsyncClient(
                base_url="http://127.0.0.1:4096",
                transport=httpx.MockTransport(handler),
            )
            try:
                with self.assertRaises(httpx.HTTPStatusError):
                    await client.send_prompt("ses_test", "Do work")
            finally:
                await client.close()

            self.assertTrue(tracker.snapshot().exhausted)
            self.assertEqual(tracker.snapshot().remaining, 0)


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
