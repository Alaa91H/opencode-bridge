"""Agent answers must reach Telegram as readable pages, not raw Markdown syntax.

Telegram renders none of Markdown: `##`, `**bold**` and `-` bullets arrive
verbatim. These tests pin the rendering (structure, spacing, escaping), the
topic-aware paging that never cuts a section in half, and the plain-text twin
that keeps a client which rejects HTML readable instead of failing.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.telegram.rendering.markdown_html import (
    build_pages,
    extract_links,
    first_page,
    render_markdown_html,
)
from bridge.telegram.rich_delivery import send_long_text


class InlineRenderingTests(unittest.TestCase):
    def test_plain_arabic_text_is_untouched(self) -> None:
        self.assertEqual(render_markdown_html("الجواب النهائي."), "الجواب النهائي.")

    def test_headings_become_titled_sections_with_a_divider(self) -> None:
        html = render_markdown_html("# نشرة يومية\n\n## عاجل\n\nنص الخبر")
        self.assertIn("<b>نشرة يومية</b>", html)
        self.assertIn("▌ عاجل", html)
        self.assertIn("────────────", html)
        self.assertNotIn("#", html)

    def test_inline_styles_become_telegram_html(self) -> None:
        html = render_markdown_html(
            "**عريض** و *مائل* و ~~ملغى~~ و `code` و [مصدر](https://example.com/a)"
        )
        self.assertIn("<b>عريض</b>", html)
        self.assertIn("<i>مائل</i>", html)
        self.assertIn("<s>ملغى</s>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn('<a href="https://example.com/a">مصدر</a>', html)

    def test_html_special_characters_are_escaped(self) -> None:
        html = render_markdown_html("a < b & c > d")
        self.assertIn("&lt;", html)
        self.assertIn("&amp;", html)
        self.assertIn("&gt;", html)

    def test_bullets_and_quotes_get_their_own_marks(self) -> None:
        html = render_markdown_html("- أول\n- ثانٍ\n\n> اقتباس مهم")
        self.assertIn("• أول", html)
        self.assertIn("• ثانٍ", html)
        self.assertIn("<blockquote>اقتباس مهم</blockquote>", html)

    def test_images_become_photo_placeholders(self) -> None:
        html = render_markdown_html("![لقطة شاشة](https://example.com/a.png)")
        self.assertIn("[صورة: لقطة شاشة]", html)
        self.assertNotIn("<a href", html)

    def test_fenced_code_stays_whole_and_keeps_indentation(self) -> None:
        html = render_markdown_html("```python\ndef f():\n    return 1\n```")
        self.assertIn("<pre>def f():\n    return 1</pre>", html)


class PagingTests(unittest.TestCase):
    def test_topics_are_never_cut_in_half(self) -> None:
        text = "\n\n".join(
            f"## عنوان {index}\n\n" + "\n".join(f"سطر {index}.{line}" for line in range(20))
            for index in range(6)
        )
        pages = build_pages(text, max_len=700)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page.html), 700)
        self.assertTrue(pages[0].html.startswith("<b>▌ عنوان 0</b>"))

    def test_one_unbroken_paragraph_is_split_to_fit(self) -> None:
        # A single 45k-character line (what a whole-report reply looks like)
        # must still page: the overflow file contract depends on this.
        pages = build_pages("كلمة " * 9000, max_len=3800)
        self.assertGreater(len(pages), 9)
        self.assertTrue(all(len(page.html) <= 3800 for page in pages))

    def test_one_oversized_topic_still_splits_on_line_boundaries(self) -> None:
        text = "## قسم ضخم\n\n" + "\n".join(f"سطر رقم {index}" for index in range(200))
        pages = build_pages(text, max_len=500)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page.html) <= 500 for page in pages))

    def test_code_block_is_never_split_between_pages(self) -> None:
        text = "## قسم\n\n```\n" + ("سطر\n" * 300) + "```\n\nخاتمة"
        for page in build_pages(text, max_len=400):
            self.assertEqual(page.html.count("<pre>"), page.html.count("</pre>"))

    def test_links_are_collected_per_page(self) -> None:
        text = "## أ\n\n[م1](https://example.com/1)\n\n## ب\n\n[م2](https://example.com/2)"
        pages = build_pages(text, max_len=4000)
        # A heading always starts a page, so each topic page carries its own links.
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].links, (("م1", "https://example.com/1"),))
        self.assertEqual(pages[1].links, (("م2", "https://example.com/2"),))

    def test_plain_twin_has_no_markup_and_keeps_text(self) -> None:
        page = first_page("# نشرة\n\n**عريض** ونص", max_len=4000)
        assert page is not None
        self.assertNotIn("<b>", page.plain)
        self.assertIn("عريض", page.plain)
        self.assertIn("نشرة", page.plain)

    def test_empty_answer_produces_nothing(self) -> None:
        self.assertEqual(build_pages("   \n\n  "), [])
        self.assertIsNone(first_page(""))

    def test_link_extraction_prefers_markdown_then_bare_urls(self) -> None:
        self.assertEqual(
            extract_links("المصدر https://example.com/story"),
            [("افتح الصفحة", "https://example.com/story")],
        )
        self.assertEqual(
            extract_links("[اسم](https://example.com/a) https://example.com/b"),
            [("اسم", "https://example.com/a")],
        )


class PageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_are_sent_as_html_with_a_page_counter(self) -> None:
        bot = AsyncMock()
        bot.send_message.return_value = SimpleNamespace(message_id=5)
        text = "\n\n".join(
            f"## قسم {index}\n\n" + "\n".join(f"سطر {index}.{line}" for line in range(20))
            for index in range(8)
        )
        ids = await send_long_text(bot, 42, text)
        self.assertGreater(len(ids), 1)
        calls = bot.send_message.await_args_list
        self.assertTrue(all(call.kwargs["parse_mode"] == "HTML" for call in calls))
        self.assertIn("<b>▌ قسم 0</b>", calls[0].kwargs["text"])
        self.assertRegex(calls[-1].kwargs["text"], r"\(\d+/\d+\)")

    async def test_html_rejection_falls_back_to_readable_plain_text(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = [
            RuntimeError("Bad Request: can't parse entities"),
            SimpleNamespace(message_id=11),
        ]
        ids = await send_long_text(bot, 42, "**عريض** ونص")
        self.assertEqual(ids, [11])
        retry = bot.send_message.await_args.kwargs
        self.assertNotIn("parse_mode", retry)
        self.assertNotIn("<b>", retry["text"])
        self.assertIn("عريض", retry["text"])


class FinalizeRenderingTests(unittest.IsolatedAsyncioTestCase):
    def _reporter(self, bot):
        from progress import ProgressStore
        from progress_reporter import LiveProgressReporter

        task = SimpleNamespace(id=3, owner_id="u", chat_id=7, status_message_id=None)
        reporter = LiveProgressReporter(task, bot, AsyncMock(), ProgressStore())
        reporter.progress.message_id = 4
        return reporter

    async def test_final_answer_replaces_the_status_message_as_html(self) -> None:
        bot = AsyncMock()
        await self._reporter(bot).finalize_text("## عنوان\n\n**عريض** ونص")
        kwargs = bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertIn("<b>▌ عنوان</b>", kwargs["text"])
        self.assertIn("<b>عريض</b>", kwargs["text"])
        self.assertEqual(kwargs["message_id"], 4)

    async def test_final_answer_falls_back_to_plain_when_html_is_rejected(self) -> None:
        bot = AsyncMock()
        bot.edit_message_text.side_effect = [
            RuntimeError("Bad Request: can't parse entities"),
            SimpleNamespace(message_id=4),
        ]
        await self._reporter(bot).finalize_text("**عريض** ونص")
        retry = bot.edit_message_text.await_args.kwargs
        self.assertNotIn("parse_mode", retry)
        self.assertEqual(retry["text"], "عريض ونص")


if __name__ == "__main__":
    unittest.main()
