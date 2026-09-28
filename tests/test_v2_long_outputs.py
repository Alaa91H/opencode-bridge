import hashlib
import unittest
from unittest.mock import AsyncMock

from bridge.domain.tasks.outputs import LongOutputComposer
from bridge.services.output_service import OutputService


class LongOutputTests(unittest.IsolatedAsyncioTestCase):
    def test_huge_result_payload_is_byte_exact_and_manifested(self):
        content = ("مرحبا\n" + "x" * 1000) * 10000
        manifest, payload = LongOutputComposer().compose("task", content)
        self.assertEqual(payload.decode("utf-8"), content)
        self.assertEqual(manifest.full.size_bytes, len(payload))
        self.assertEqual(manifest.full.sha256, hashlib.sha256(payload).hexdigest())
        self.assertLess(len(manifest.summary), len(content))
        self.assertTrue(manifest.full.name.endswith(".md"))

    def test_optional_pages_reconstruct_full_unicode_content(self):
        content = "أبجد" * 10000
        manifest, payload = LongOutputComposer(page_chars=777).compose("t", content, paginate=True)
        self.assertEqual("".join(manifest.pages), content)
        self.assertEqual(payload.decode(), content)

    def test_short_result_summary_is_complete(self):
        content = "complete"
        manifest, payload = LongOutputComposer().compose("t", content)
        self.assertEqual(manifest.summary, content)
        self.assertEqual(payload.decode(), content)

    async def test_full_artifact_is_stored_not_summary(self):
        store = AsyncMock(return_value="artifact://result")
        service = OutputService(store_artifact=store, composer=LongOutputComposer(summary_chars=10))
        content = "0123456789" * 1000
        manifest, location = await service.prepare("t", content, markdown=False)
        self.assertEqual(location, "artifact://result")
        stored = store.await_args.args[1]
        self.assertEqual(stored.decode(), content)
        self.assertEqual(manifest.full.media_type, "text/plain")
