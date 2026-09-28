from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from attachments import AttachmentStore
from bridge.config.settings import BridgeSettings, SettingsError
from bridge.infrastructure.telegram.capabilities import capabilities
from bridge.telegram.execution import TelegramExecutionDelivery


class TelegramTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_cloud_is_default_and_local_capabilities_are_detected(self):
        cloud = BridgeSettings.load(env={})
        self.assertEqual(cloud.telegram.api_mode, "cloud")
        self.assertFalse(capabilities(cloud.telegram).local_paths)

        local = BridgeSettings.load(env={
            "TELEGRAM_API_MODE": "local",
            "TELEGRAM_LOCAL_API_BASE_URL": "http://127.0.0.1:8081/bot",
            "TELEGRAM_LOCAL_FILE_BASE_URL": "http://127.0.0.1:8081/file/bot",
        })
        caps = capabilities(local.telegram)
        self.assertEqual(caps.mode, "local")
        self.assertTrue(caps.local_paths)
        self.assertTrue(caps.streaming_download)
        self.assertTrue(caps.streaming_upload)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(SettingsError):
            BridgeSettings.load(env={"TELEGRAM_API_MODE": "magic"})

    async def test_local_file_path_is_stream_copied_without_download_buffer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "telegram-local.bin"
            payload = b"x" * (3 * 1024 * 1024 + 17)
            source.write_bytes(payload)
            remote = SimpleNamespace(file_path=str(source), download_to_drive=AsyncMock())
            bot = SimpleNamespace(local_mode=True, get_file=AsyncMock(return_value=remote))
            media = SimpleNamespace(file_size=len(payload), file_id="f", file_name="large.bin", mime_type="application/octet-stream")
            message = SimpleNamespace(document=media, photo=None, video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None)
            store = AttachmentStore(root / "managed", max_bytes=8 * 1024 * 1024)
            saved = await store.download_from_message(message, bot, "42")
            self.assertEqual(Path(saved.path).read_bytes(), payload)
            remote.download_to_drive.assert_not_awaited()

    async def test_cloud_download_streams_to_drive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            async def download_to_drive(custom_path):
                Path(custom_path).write_bytes(b"cloud")
            remote = SimpleNamespace(file_path="files/a", download_to_drive=AsyncMock(side_effect=download_to_drive))
            bot = SimpleNamespace(local_mode=False, get_file=AsyncMock(return_value=remote))
            media = SimpleNamespace(file_size=5, file_id="f", file_name="a.bin", mime_type="application/octet-stream")
            message = SimpleNamespace(document=media, photo=None, video=None, audio=None, voice=None, animation=None, video_note=None, sticker=None)
            store = AttachmentStore(root / "managed")
            saved = await store.download_from_message(message, bot, "42")
            self.assertEqual(Path(saved.path).read_bytes(), b"cloud")
            remote.download_to_drive.assert_awaited_once()

    async def test_output_upload_passes_file_handle_not_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "out.bin"
            path.write_bytes(b"z" * (2 * 1024 * 1024))
            bot = SimpleNamespace(send_document=AsyncMock())
            delivery = TelegramExecutionDelivery(bot, None, None, {}, SimpleNamespace(), max_message_length=4096, error_message=str)
            task = SimpleNamespace(chat_id=7)
            self.assertEqual(await delivery.send_outputs(task, [path]), 1)
            document = bot.send_document.await_args.kwargs["document"]
            self.assertFalse(isinstance(document, (bytes, bytearray)))
