"""Deliver a digest the way global news channels do: one item per message.

A digest answer is a list of items, not a wall of text: a bold headline with a
date, then labelled bullets (what happened, details, practical context,
analysis) and a source link. Global channels render exactly that as one message
per item, each with its own photo and source button, and this module does the
same here.

Items are recognised by shape — a bold headline line followed by bullets — so an
answer that is not a digest keeps the generic section rendering untouched. The
section a headline sits under becomes that item's page label, document order is
preserved, and the images written inside an item travel with that item's page
instead of trailing the whole answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bridge.telegram.rendering.collapse_guard import truncated_answer
from bridge.telegram.rendering.markdown_html import (
    MAX_PAGE_CHARS,
    Page,
    build_pages,
    extract_links,
)

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]{0,200})\]\((?P<url>https?://[^\s)]+)\)")
_STANDALONE_IMAGE_RE = re.compile(
    r"^[ \t]*(?P<url>https?://[^\s<>()]+\.(?:jpe?g|png|webp|gif)(?:\?[^\s<>()]*)?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
_ITEM_HEAD_RE = re.compile(r"^\*\*(?P<headline>[^*]{6,220})\*\*(?P<tail>.*)$")
_TAIL_RE = re.compile(r"^[—–-]\s")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+|◦\s+|•\s+)")

MAX_ITEM_IMAGES = 3


@dataclass(frozen=True)
class _Chunk:
    section: str
    kind: str
    lines: tuple[str, ...]


def _images(text: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _IMAGE_RE.finditer(text or ""):
        url = match.group("url").strip()
        if url in seen:
            continue
        seen.add(url)
        found.append((match.group("alt").strip() or "صورة", url))
        if len(found) >= MAX_ITEM_IMAGES:
            break
    for match in _STANDALONE_IMAGE_RE.finditer(text or ""):
        url = match.group("url").strip()
        if url in seen:
            continue
        seen.add(url)
        stem = url.rsplit("/", 1)[-1].split("?", 1)[0].rsplit(".", 1)[0]
        found.append((stem[:200] or "صورة", url))
        if len(found) >= MAX_ITEM_IMAGES:
            break
    return tuple(found)


def _is_item_head(lines: list[str], index: int) -> bool:
    match = _ITEM_HEAD_RE.match(lines[index].strip())
    if match is None:
        return False
    tail = match.group("tail").strip()
    # "**النطاق الزمني:** آخر 24 ساعة" is bold prose, not a headline.
    if tail and not _TAIL_RE.match(tail):
        return False
    for candidate in lines[index + 1 : index + 4]:
        if not candidate.strip():
            continue
        return _BULLET_RE.match(candidate) is not None
    return False


def _item_body(lines: list[str], index: int) -> tuple[list[str], int]:
    body = [lines[index]]
    cursor = index + 1
    while cursor < len(lines):
        line = lines[cursor]
        stripped = line.strip()
        if not stripped:
            following = cursor + 1
            if following < len(lines) and _BULLET_RE.match(lines[following]):
                body.append(line)
                cursor += 1
                continue
            break
        if _SECTION_RE.match(stripped) or _ITEM_HEAD_RE.match(stripped):
            break
        body.append(line)
        cursor += 1
    return body, cursor


def _chunks(text: str) -> list[_Chunk]:
    lines = (text or "").splitlines()
    chunks: list[_Chunk] = []
    buffer: list[str] = []
    section = ""
    pending_heading: str | None = None

    def flush() -> None:
        nonlocal buffer
        if any(line.strip() for line in buffer):
            chunks.append(_Chunk(section, "text", tuple(buffer)))
        buffer = []

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        heading = _SECTION_RE.match(stripped)
        if heading is not None:
            flush()
            section = heading.group("title").strip()
            pending_heading = line
            index += 1
            continue
        if _is_item_head(lines, index):
            flush()
            body, index = _item_body(lines, index)
            chunks.append(_Chunk(section, "item", tuple(body)))
            pending_heading = None
            continue
        if not stripped:
            # A blank line neither ends an item nor materializes a pending
            # heading: `## عاجل` followed by its first item must not become a
            # page of its own.
            index += 1
            continue
        if pending_heading is not None:
            buffer.append(pending_heading)
            pending_heading = None
        buffer.append(line)
        index += 1
    if pending_heading is not None:
        buffer.append(pending_heading)
    flush()
    return chunks


def _section_label(section: str) -> str:
    return f"**▎ {section}**" if section else ""


def build_news_pages(text: str, *, max_len: int = MAX_PAGE_CHARS) -> list[Page]:
    """Render a digest item by item, falling back to section pages elsewhere."""
    # A model that loops must never be delivered page after page of repetition,
    # so the collapse is cut here — the single place every delivery path renders
    # through — and the complete raw text is handed over as a file instead.
    text, _truncated = truncated_answer(text)
    # One decision for the whole answer: a summary that keeps `[3]` while the
    # item it points to dropped it reads as a broken cross-reference.
    strip_citations = bool(extract_links(text or ""))
    pages: list[Page] = []
    for chunk in _chunks(text):
        raw_source = "\n".join(chunk.lines)
        source = _STANDALONE_IMAGE_RE.sub("", raw_source)
        produced = build_pages(source, max_len=max_len, strip_citations=strip_citations)
        if not produced:
            continue
        if chunk.kind != "item":
            pages.extend(produced)
            continue
        label = _section_label(chunk.section)
        body = f"{label}\n\n{source}" if label else source
        rendered = build_pages(
            body, max_len=max_len, strip_citations=strip_citations
        )
        if not rendered:
            continue
        first = rendered[0]
        images = _images(raw_source)
        pages.append(Page(first.html, first.plain, first.links, images))
        pages.extend(rendered[1:])
    return pages


def delivery_pages(text: str, *, max_len: int = MAX_PAGE_CHARS) -> list[Page]:
    """The pages one answer is delivered as: one per news item when shaped like one."""
    return build_news_pages(text, max_len=max_len)
