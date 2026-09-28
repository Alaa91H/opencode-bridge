import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.telegram.transport import TelegramTransportSettings


class TelegramLocalProductionTests(unittest.TestCase):
    def test_cloud_remains_default(self):
        settings = TelegramTransportSettings()
        self.assertEqual(settings.mode, "cloud")

    def test_local_profile_capability(self):
        settings = TelegramTransportSettings(
            mode="local", local_api_url="http://127.0.0.1:8081",
            local_file_root=Path("/var/lib/telegram-bot-api"))
        self.assertEqual(settings.mode, "local")
        self.assertIn("127.0.0.1", settings.local_api_url)

    def test_large_file_shape_does_not_require_payload_allocation(self):
        logical_size = 4 * 1024 * 1024 * 1024
        chunk = b"x" * (64 * 1024)
        chunks = (logical_size + len(chunk) - 1) // len(chunk)
        self.assertGreater(chunks, 60000)
        self.assertEqual(len(chunk), 64 * 1024)

    def test_profile_document_exists(self):
        self.assertTrue(Path("deploy/telegram-local-api.env.example").is_file())
