"""A pre-rendered page must reach Telegram as HTML, not as visible markup.

`/skills` built a finished HTML page and then handed it to the Markdown
renderer, which escaped the markup it already contained: the owner read
``<b>heading</b>`` and ``&gt;`` as literal words. These tests pin the contract —
a rendered page is sent as HTML, its plain twin is the fallback, and the renderer
is never applied twice.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.telegram.commands.skills import SkillCommands
from bridge.telegram.rendering import skills as view
from bridge.telegram.rendering.markdown_html import html_to_plain, render_markdown_html
from bridge.telegram.rich_delivery import send_html

PAGE = view.skills_page(
    source="https://example.com/skills",
    skills=(view.SkillView(name="research-notes", description="Reads sources."),),
    commit="abcdef1234567890",
    synced_at=0.0,
    last_status="synced",
    last_error="",
    needs_reload=False,
)


class _Message:
    def __init__(self, *, reject_html: bool = False) -> None:
        self.reject_html = reject_html
        self.sent: list[dict[str, object]] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:
        if self.reject_html and kwargs.get("parse_mode") == "HTML":
            raise RuntimeError("Bad Request: can't parse entities")
        self.sent.append({"text": text, **kwargs})


class SendHtmlTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_rendered_page_is_sent_as_html(self) -> None:
        message = _Message()
        self.assertTrue(await send_html(message, PAGE, fallback=html_to_plain(PAGE)))
        self.assertEqual(message.sent[0]["parse_mode"], "HTML")
        self.assertEqual(message.sent[0]["text"], PAGE)

    async def test_a_rejected_format_falls_back_to_readable_words(self) -> None:
        message = _Message(reject_html=True)
        self.assertTrue(await send_html(message, PAGE, fallback=html_to_plain(PAGE)))
        self.assertEqual(len(message.sent), 1, "the rejected HTML must not be sent twice")
        plain = str(message.sent[0]["text"])
        self.assertNotIn("parse_mode", message.sent[0])
        self.assertNotIn("<b>", plain)
        self.assertNotIn("&gt;", plain)
        self.assertIn("مهارات الوكيل", plain)

    async def test_the_fallback_keeps_the_words_an_escape_had_hidden(self) -> None:
        plain = html_to_plain(PAGE)
        self.assertIn("research-notes", plain)
        self.assertIn("https://example.com/skills", plain)
        self.assertNotIn("&amp;", plain)


class SkillCommandDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def _commands(self, message: _Message) -> SkillCommands:
        service = SimpleNamespace(
            status=lambda: {
                "source": "https://example.com/skills",
                "installed": (SimpleNamespace(name="research-notes", description="Reads."),),
                "commit": "abcdef1234567890",
                "synced_at": 0.0,
                "last_status": "synced",
                "last_error": "",
                "needs_reload": False,
            },
            sync=AsyncMock(),
        )
        commands = SkillCommands(
            service,
            reply=AsyncMock(),
            error_message=lambda exc, what: what,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=7),
        )
        return commands, update

    async def test_listing_skills_never_renders_the_page_twice(self) -> None:
        message = _Message()
        commands, update = self._commands(message)
        await commands.skills(update, SimpleNamespace(args=[]))
        sent = str(message.sent[0]["text"])
        self.assertEqual(message.sent[0]["parse_mode"], "HTML")
        self.assertIn("<b>", sent)
        self.assertNotIn("&lt;b&gt;", sent)
        self.assertNotIn("&amp;lt;", sent)

    async def test_a_markdown_renderer_would_have_mangled_it(self) -> None:
        """The trap this test exists for: rendering an HTML page as Markdown."""
        mangled = render_markdown_html(PAGE)
        self.assertIn("&lt;b&gt;", mangled)
        self.assertNotEqual(mangled, PAGE)


if __name__ == "__main__":
    unittest.main()
