"""Regression tests for rich Telegram delivery helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.telegram.rich_delivery import (
    MAX_BUTTONS,
    build_link_keyboard,
    chunk_rich_text,
    extract_images,
    extract_links,
    preview_options,
    send_long_text,
    send_stream_draft,
    strip_image_markup,
)


class ImageExtractionTests(unittest.TestCase):
    def test_images_are_extracted_and_deduplicated(self) -> None:
        text = ("انظر ![صورة أولى](https://example.com/a.jpg) ثم "
                "![صورة أولى](https://example.com/a.jpg) و"
                "![ثانية](https://example.com/b.png)")
        images = extract_images(text)
        self.assertEqual([url for _alt, url in images],
                         ["https://example.com/a.jpg", "https://example.com/b.png"])

    def test_plain_links_are_not_images(self) -> None:
        self.assertEqual(extract_images("[مصدر](https://example.com/x)"), [])

    def test_standalone_image_url_on_its_own_line_is_a_photo(self) -> None:
        images = extract_images("نص الخبر\nhttps://example.com/cover.webp?w=800\nبقية النص")
        self.assertEqual(images, [("cover", "https://example.com/cover.webp?w=800")])

    def test_image_markup_is_stripped_from_text_chunks(self) -> None:
        stripped = strip_image_markup("خبر ![الغلاف](https://example.com/c.jpg) مهم")
        self.assertNotIn("!(", stripped)
        self.assertIn("الغلاف", stripped)

    def test_standalone_image_url_is_stripped_from_text(self) -> None:
        stripped = strip_image_markup("خبر\nhttps://example.com/c.jpg\nنهاية")
        self.assertNotIn("https://example.com/c.jpg", stripped)
        self.assertIn("نهاية", stripped)


class LinkExtractionTests(unittest.TestCase):
    def test_markdown_links_are_capped(self) -> None:
        text = " ".join(f"[م{i}](https://example.com/{i})" for i in range(12))
        self.assertEqual(len(extract_links(text)), MAX_BUTTONS)

    def test_bare_urls_are_a_fallback(self) -> None:
        links = extract_links("المصدر https://example.com/story تابع")
        self.assertEqual(links, [("افتح الصفحة", "https://example.com/story")])


class ChunkingTests(unittest.TestCase):
    def test_long_text_splits_into_bounded_chunks(self) -> None:
        text = "word " * 3000
        chunks = chunk_rich_text(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 4000)

    def test_keyboard_is_none_without_links(self) -> None:
        self.assertIsNone(build_link_keyboard([]))

    def test_keyboard_has_one_row_per_link(self) -> None:
        keyboard = build_link_keyboard([("a", "https://example.com/1"),
                                        ("b", "https://example.com/2")])
        self.assertIsNotNone(keyboard)
        assert keyboard is not None
        self.assertEqual(len(keyboard.inline_keyboard), 2)


class PreviewOptionsTests(unittest.TestCase):
    def test_pinned_url_requests_large_media(self) -> None:
        options = preview_options("https://example.com/story")
        link_options = options["link_preview_options"]
        self.assertEqual(link_options.url, "https://example.com/story")
        self.assertTrue(link_options.prefer_large_media)
        self.assertFalse(link_options.is_disabled)

    def test_plain_and_disabled_variants(self) -> None:
        self.assertFalse(preview_options()["link_preview_options"].is_disabled)
        self.assertTrue(preview_options(disabled=True)["link_preview_options"].is_disabled)


class SendLongTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_chunk_pins_first_source_preview(self) -> None:
        bot = AsyncMock()
        bot.send_message.return_value = SimpleNamespace(message_id=7)
        ids = await send_long_text(bot, 42, "خبر [مصدر](https://example.com/s) مهم")
        self.assertEqual(ids, [7])
        options = bot.send_message.await_args.kwargs["link_preview_options"]
        self.assertEqual(options.url, "https://example.com/s")
        self.assertTrue(options.prefer_large_media)
        self.assertNotIn("disable_web_page_preview", bot.send_message.await_args.kwargs)

    async def test_failing_send_retries_without_preview(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = [RuntimeError("blocked"),
                                        SimpleNamespace(message_id=9)]
        ids = await send_long_text(bot, 42, "نص بلا روابط")
        self.assertEqual(ids, [9])
        retry_options = bot.send_message.await_args.kwargs["link_preview_options"]
        self.assertTrue(retry_options.is_disabled)


class StreamDraftTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_draft_carries_chat_draft_and_text(self) -> None:
        bot = AsyncMock()
        self.assertTrue(await send_stream_draft(bot, 42, 7, "مرحبا"))
        kwargs = bot.send_message_draft.await_args.kwargs
        self.assertEqual(
            (kwargs["chat_id"], kwargs["draft_id"], kwargs["text"]), (42, 7, "مرحبا"))

    async def test_missing_method_zero_id_and_failure_decline(self) -> None:
        self.assertFalse(await send_stream_draft(object(), 1, 7, "x"))
        bot = AsyncMock()
        self.assertFalse(await send_stream_draft(bot, 1, 0, "x"))
        bot.send_message_draft.side_effect = RuntimeError("groups unsupported")
        self.assertFalse(await send_stream_draft(bot, 1, 7, "x"))

    async def test_text_is_capped_at_api_limit(self) -> None:
        bot = AsyncMock()
        await send_stream_draft(bot, 1, 3, "y" * 5000)
        self.assertEqual(len(bot.send_message_draft.await_args.kwargs["text"]), 4096)


class ReporterDraftBranchTests(unittest.IsolatedAsyncioTestCase):
    def _reporter(self, bot, *, drafts: bool = False):
        from progress import ProgressStore
        from progress_reporter import LiveProgressReporter

        task = SimpleNamespace(id=11, owner_id="u", chat_id=99)
        reporter = LiveProgressReporter(task, bot, object(), ProgressStore())
        reporter._live_enabled = True
        reporter._stream_drafts = drafts
        reporter.progress.message_id = 5
        reporter._live_buffer = "مرحبا"
        return reporter

    async def test_draft_path_skips_edit(self) -> None:
        bot = AsyncMock()
        await self._reporter(bot, drafts=True)._publish_live(flush=True)
        bot.send_message_draft.assert_awaited_once()
        bot.edit_message_text.assert_not_awaited()

    async def test_draft_failure_falls_back_to_edit(self) -> None:
        bot = AsyncMock()
        bot.send_message_draft.side_effect = RuntimeError("unsupported chat")
        await self._reporter(bot, drafts=True)._publish_live(flush=True)
        bot.edit_message_text.assert_awaited_once()

    async def test_flag_off_keeps_edit_path(self) -> None:
        bot = AsyncMock()
        await self._reporter(bot, drafts=False)._publish_live(flush=True)
        bot.send_message_draft.assert_not_awaited()
        bot.edit_message_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
