"""Rich Telegram delivery: chunked long texts, photos, and open-page buttons.

Agent answers (news digests, daily briefings, topic lists) are Markdown with
``![caption](https-url)`` images and ``[name](https-url)`` sources. Telegram can
only show ~4096 chars per message and shows no images unless they are sent as
photos. This module turns one long Markdown answer into a professional
sequence:

1. status message is replaced by the first chunk (existing behaviour),
2. extra chunks are sent as follow-up messages (no more silent truncation),
3. up to 10 real ``https`` images are re-sent as photos with captions,
4. source links become inline URL buttons (``افتح الصفحة``) so every topic can
   open its page.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, LinkPreviewOptions

from bridge.telegram.rendering.markdown_html import Page, build_pages
from bridge.telegram.rendering.markdown_html import extract_links as _page_links
from formatter import chunk_message

logger = logging.getLogger(__name__)

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]{0,200})\]\((?P<url>https?://[^\s)]+)\)")

MAX_FOLLOWUPS = 8
MAX_PHOTOS = 10
MAX_BUTTONS = 8
MAX_CHUNK = 4000


def extract_images(text: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for match in _IMAGE_RE.finditer(text or ""):
        url = match.group("url").strip()
        if url in seen:
            continue
        seen.add(url)
        found.append((match.group("alt").strip() or "صورة", url))
        if len(found) >= MAX_PHOTOS:
            break
    return found


def extract_links(text: str) -> list[tuple[str, str]]:
    """Source links for the open-page keyboard, capped for one reply markup."""
    return _page_links(text, limit=MAX_BUTTONS)


def strip_image_markup(text: str) -> str:
    return _IMAGE_RE.sub(r"[صورة: \g<alt>]", text or "")


def chunk_rich_text(text: str, *, max_len: int = MAX_CHUNK) -> list[str]:
    body = (text or "").strip()
    if not body:
        return []
    return chunk_message(strip_image_markup(body), max_len=max_len)


def build_link_keyboard(links: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    if not links:
        return None
    rows = [
        [InlineKeyboardButton((label[:40] or "افتح الصفحة"), url=url)]
        for label, url in links[:MAX_BUTTONS]
    ]
    return InlineKeyboardMarkup(rows)


def preview_options(url: str | None = None, *, disabled: bool = False) -> dict[str, Any]:
    """Link-preview options for the pinned PTB 22.8 (available since 20.8).

    The first digest chunk pins its first source URL with a large preview;
    follow-ups leave previews enabled by default. Never uses the legacy
    `disable_web_page_preview` flag on this path.
    """
    if url:
        return {"link_preview_options": LinkPreviewOptions(url=url, prefer_large_media=True)}
    return {"link_preview_options": LinkPreviewOptions(is_disabled=disabled)}


async def send_photos(
    bot: Any, chat_id: int, images: list[tuple[str, str]]
) -> int:
    """Send source images as real photos. Returns successfully sent count."""
    sent = 0
    # First try a single media group for speed; fall back to one-by-one.
    group = [
        InputMediaPhoto(media=url, caption=alt[:200] or "صورة")
        for alt, url in images[:MAX_PHOTOS]
    ]
    if len(group) > 1:
        try:
            await bot.send_media_group(chat_id=chat_id, media=group[:MAX_PHOTOS])
            return len(group[:MAX_PHOTOS])
        except Exception:
            pass
    for alt, url in images[:MAX_PHOTOS]:
        try:
            await bot.send_photo(
                chat_id=chat_id, photo=url, caption=alt[:200] or "صورة"
            )
            sent += 1
        except Exception:
            logger.debug("photo %s was rejected by Telegram", url, exc_info=True)
            continue
    return sent


async def send_rendered_pages(
    bot: Any,
    chat_id: int,
    pages: list[Page],
    *,
    start_index: int = 0,
    with_links: bool = True,
    reply_to_message_id: int | None = None,
) -> list[int]:
    """Send pre-rendered pages as HTML messages, with a plain-text safety net.

    Each page carries its own source buttons so a topic stays self-contained,
    and a client that rejects HTML still receives a readable answer instead of
    a silent failure.
    """
    if not pages:
        return []
    sent_ids: list[int] = []
    total = min(len(pages), MAX_FOLLOWUPS + 1)
    for index in range(start_index, total):
        page = pages[index]
        suffix = f"\n\n<i>({index + 1}/{total})</i>" if total > 1 else ""
        links = list(page.links) if with_links else []
        keyboard = build_link_keyboard(links)
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": page.html + suffix,
            "parse_mode": "HTML",
        }
        # First page pins the first source with a large preview; the rest keep
        # default previews so every topic stays visual.
        kwargs.update(preview_options(links[0][1] if index == 0 and links else None))
        if reply_to_message_id is not None and index == start_index:
            kwargs["reply_to_message_id"] = reply_to_message_id
        # Only the last page carries open-page buttons so a topic page is never
        # stripped of its own links by a failure on an earlier one.
        if index == total - 1 and keyboard is not None:
            kwargs["reply_markup"] = keyboard
        try:
            message = await bot.send_message(**kwargs)
            sent_ids.append(int(getattr(message, "message_id", 0) or 0))
        except Exception:
            fallback: dict[str, Any] = {
                "chat_id": chat_id,
                "text": page.plain + (f"\n\n({index + 1}/{total})" if total > 1 else ""),
            }
            fallback.update(preview_options(disabled=True))
            if index == start_index and reply_to_message_id is not None:
                fallback["reply_to_message_id"] = reply_to_message_id
            if index == total - 1 and keyboard is not None:
                fallback["reply_markup"] = keyboard
            try:
                message = await bot.send_message(**fallback)
                sent_ids.append(int(getattr(message, "message_id", 0) or 0))
            except Exception:
                logger.debug("message page %s was rejected twice", index, exc_info=True)
                continue
    if len(pages) > total:
        try:
            message = await bot.send_message(
                chat_id=chat_id,
                text="… تتمة النشرة في الملف المرفق answer-full.md.",
                **preview_options(disabled=True),
            )
            sent_ids.append(int(getattr(message, "message_id", 0) or 0))
        except Exception:
            pass
    return sent_ids


async def send_long_text(
    bot: Any,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    with_links: bool = True,
) -> list[int]:
    """Send a long Markdown answer as several professional HTML pages."""
    return await send_rendered_pages(
        bot,
        chat_id,
        build_pages(text),
        with_links=with_links,
        reply_to_message_id=reply_to_message_id,
    )


async def send_stream_draft(bot: Any, chat_id: int, draft_id: int, text: str) -> bool:
    """Best-effort animated streaming draft (Bot API 9.3+, PTB>=22.6).

    Returns True when the draft was accepted, False on any failure —
    including bots without the method and non-private chats, where the
    endpoint itself rejects. Callers fall back to message edits.
    """
    sender = getattr(bot, "send_message_draft", None)
    if not callable(sender):
        return False
    try:
        draft = int(draft_id)
        if draft == 0:
            return False
        await sender(chat_id=chat_id, draft_id=draft, text=(text or "")[:4096])
        return True
    except Exception:
        return False
