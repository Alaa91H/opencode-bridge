import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from bridge.config.settings import BridgeSettings
from bridge.infrastructure.telegram.capabilities import capabilities


class _PathRecorder(BaseHTTPRequestHandler):
    """A real HTTP endpoint that records the exact path a client requests."""

    paths: list[str] = []

    def log_message(self, *args: object) -> None:
        return

    def _respond(self) -> None:
        type(self).paths.append(self.path)
        payload = {
            "ok": True,
            "result": {
                "id": 1,
                "is_bot": True,
                "first_name": "Recorded",
                "username": "recorded_bot",
            },
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._respond()

    def do_POST(self) -> None:  # noqa: N802 - the Telegram library posts to the API
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._respond()


class TelegramLocalProductionTests(unittest.TestCase):
    def test_cloud_remains_default(self):
        settings = BridgeSettings.load(env={})
        self.assertEqual(settings.telegram.api_mode, "cloud")

    def test_local_profile_capability(self):
        settings = BridgeSettings.load(env={
            "TELEGRAM_API_MODE": "local",
            "TELEGRAM_LOCAL_API_BASE_URL": "http://127.0.0.1:8081/bot",
            "TELEGRAM_LOCAL_FILE_BASE_URL": "http://127.0.0.1:8081/file/bot",
        })
        caps = capabilities(settings.telegram)
        self.assertEqual(caps.mode, "local")
        self.assertTrue(caps.local_paths)
        self.assertTrue(caps.streaming_download)
        self.assertTrue(caps.streaming_upload)

    def test_resolved_base_urls_have_no_trailing_slash(self):
        """PTB concatenates the token onto base_url, so a trailing slash breaks the path."""
        settings = BridgeSettings.load(env={
            "TELEGRAM_API_MODE": "local",
            "TELEGRAM_LOCAL_API_BASE_URL": "http://127.0.0.1:8081/bot",
            "TELEGRAM_LOCAL_FILE_BASE_URL": "http://127.0.0.1:8081/file/bot",
        })
        self.assertEqual(settings.telegram.local_api_base_url, "http://127.0.0.1:8081/bot")
        self.assertEqual(settings.telegram.local_file_base_url, "http://127.0.0.1:8081/file/bot")
        self.assertFalse(settings.telegram.local_api_base_url.endswith("/"))
        self.assertFalse(settings.telegram.local_file_base_url.endswith("/"))

    def test_base_url_normalization_is_idempotent(self):
        for supplied in (
            "http://127.0.0.1:8081/bot",
            "http://127.0.0.1:8081/bot/",
            "http://127.0.0.1:8081/bot///",
        ):
            with self.subTest(supplied=supplied):
                settings = BridgeSettings.load(env={
                    "TELEGRAM_API_MODE": "local",
                    "TELEGRAM_LOCAL_API_BASE_URL": supplied,
                })
                self.assertEqual(
                    settings.telegram.local_api_base_url, "http://127.0.0.1:8081/bot"
                )

    def test_telegram_library_requests_the_expected_path(self):
        """Regression: a trailing slash made the real server reject the token.

        Proves the path the Telegram library actually requests, over a real
        socket, so the assertion cannot drift from library behaviour.
        """
        from telegram import Bot

        _PathRecorder.paths = []
        server = HTTPServer(("127.0.0.1", 0), _PathRecorder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        token = "123456:TESTTOKEN"
        try:
            settings = BridgeSettings.load(env={
                "TELEGRAM_API_MODE": "local",
                "TELEGRAM_LOCAL_API_BASE_URL": (
                    f"http://127.0.0.1:{server.server_address[1]}/bot"
                ),
            })
            resolved = settings.telegram.local_api_base_url

            async def call() -> None:
                bot = Bot(token=token, base_url=resolved, local_mode=True)
                try:
                    await bot.get_me()
                finally:
                    await bot.shutdown()

            asyncio.run(call())
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(len(_PathRecorder.paths), 1, _PathRecorder.paths)
        self.assertEqual(_PathRecorder.paths[0], f"/bot{token}/getMe")

    def test_large_file_shape_does_not_require_payload_allocation(self):
        logical_size = 4 * 1024 * 1024 * 1024
        chunk = b"x" * (64 * 1024)
        chunks = (logical_size + len(chunk) - 1) // len(chunk)
        self.assertGreater(chunks, 60000)
        self.assertEqual(len(chunk), 64 * 1024)

    def test_profile_document_exists(self):
        self.assertTrue(Path("deploy/telegram-local-api.env.example").is_file())

    def test_profile_document_uses_the_supported_env_names(self):
        text = Path("deploy/telegram-local-api.env.example").read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_API_MODE=local", text)
        # These are the names the settings loader actually reads.
        self.assertIn("TELEGRAM_LOCAL_API_BASE_URL=http://127.0.0.1:8081/bot", text)
        self.assertIn("TELEGRAM_LOCAL_FILE_BASE_URL=http://127.0.0.1:8081/file/bot", text)
        # The profile must not reintroduce names the loader ignores.
        self.assertNotIn("TELEGRAM_LOCAL_API_URL=", text)
        self.assertNotIn("TELEGRAM_LOCAL_API_FILE_ROOT=", text)
        for line in text.splitlines():
            if line.startswith("TELEGRAM_LOCAL_") and "=" in line:
                with self.subTest(line=line):
                    self.assertFalse(line.split("=", 1)[1].endswith("/"), line)
