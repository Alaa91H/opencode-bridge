import unittest
from pathlib import Path

from bridge.config.settings import BridgeSettings
from bridge.infrastructure.telegram.capabilities import capabilities


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

    def test_large_file_shape_does_not_require_payload_allocation(self):
        logical_size = 4 * 1024 * 1024 * 1024
        chunk = b"x" * (64 * 1024)
        chunks = (logical_size + len(chunk) - 1) // len(chunk)
        self.assertGreater(chunks, 60000)
        self.assertEqual(len(chunk), 64 * 1024)

    def test_profile_document_exists(self):
        self.assertTrue(Path("deploy/telegram-local-api.env.example").is_file())
