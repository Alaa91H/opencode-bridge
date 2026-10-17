"""Render agent Markdown into the HTML Telegram actually displays.

Telegram's HTML mode understands only b, i, u, s, code, pre, a and blockquote:
it has no headings and no lists. A raw Markdown answer therefore arrives as
noise — ``##``, ``**bold**`` and ``-`` bullets show verbatim and paragraphs run
together.

This module renders one answer into titled sections, bold and inline code,
bullet glyphs, quoted lines, dividers and clickable sources, with real blank
lines between blocks. Paging is topic-aware: a heading starts a page, a topic is
never cut in half, and a fenced code block is never split. Every page keeps a
plain-text twin so a client that rejects HTML still gets a readable answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TELEGRAM_LIMIT = 4096
# Room left for the page counter and markup overhead inside TELEGRAM_LIMIT.
MAX_PAGE_CHARS = 3800
MAX_PAGE_LINKS = 8

DIVIDER = "────────────"

_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]{0,200})\]\((?P<url>https?://[^\s)]+)\)")
_LINK_RE = re.compile(r"(?<!!)\[(?P<label>[^\]]{1,80})\]\((?P<url>https?://[^\s)]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)\s*(?P<lang>[A-Za-z0-9_+#.-]{0,24})\s*$")
_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_BULLET_RE = re.compile(r"^(?P<indent>[ \t]{0,6})[-*+]\s+(?P<body>.+)$")
_ORDERED_RE = re.compile(r"^(?P<indent>[ \t]{0,6})(?P<number>\d{1,3})[.)]\s+(?P<body>.+)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(?P<body>.*)$")
_CODE_SPAN_RE = re.compile(r"`(?P<code>[^`\n]{1,300})`")
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(?P<body>[^*]+?)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(?P<a>[^*]+?)\*\*|__(?P<b>[^_]+?)__")
_STRIKE_RE = re.compile(r"~~(?P<body>[^~]+?)~~")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?P<body>[^*\n]{1,300})\*(?![\w*])")
_ANCHOR_RE = re.compile(r'<a href="(?P<url>[^"]+)">(?P<label>[^<]*)</a>')
_LABEL_RE = re.compile(r"^(?P<label>[^:：\n]{2,24})\s*[:：]\s+(?P<rest>.+)$")

_PARAGRAPH = "paragraph"
_LIST = "list"
_HEADING = "heading"
_CODE = "code"
_DIVIDER = "divider"


@dataclass(frozen=True)
class Page:
    """One Telegram-sized page in both renderings."""

    html: str
    plain: str
    links: tuple[tuple[str, str], ...]
    images: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _Block:
    html: str
    plain: str
    kind: str = _PARAGRAPH


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def extract_links(text: str, *, limit: int = MAX_PAGE_LINKS) -> list[tuple[str, str]]:
    """Collect source links for inline buttons, Markdown first then bare URLs."""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []

    def remember(label: str, url: str) -> bool:
        cleaned = url.strip().rstrip(".,;!")
        if not cleaned or cleaned in seen:
            return True
        seen.add(cleaned)
        found.append((label.strip() or "افتح الصفحة", cleaned))
        return len(found) < limit

    for match in _LINK_RE.finditer(text or ""):
        if not remember(match.group("label"), match.group("url")):
            return found
    if not found:
        for match in _BARE_URL_RE.finditer(text or ""):
            if not remember("افتح الصفحة", match.group(0)):
                return found
    return found


def _inline(text: str) -> str:
    """Render inline Markdown to HTML, escaping literals and keeping tags."""
    tokens: list[str] = []

    def keep(html: str) -> str:
        tokens.append(html)
        return f"\x00{len(tokens) - 1}\x00"

    def image(match: re.Match[str]) -> str:
        return keep(f"[صورة: {_escape(match.group('alt').strip() or 'صورة')}]")

    def link(match: re.Match[str]) -> str:
        label = _escape(match.group("label").strip() or "افتح الصفحة")
        return keep(f'<a href="{_escape(match.group("url").strip())}">{label}</a>')

    def code(match: re.Match[str]) -> str:
        return keep(f"<code>{_escape(match.group('code').strip())}</code>")

    def bold_italic(match: re.Match[str]) -> str:
        return keep(f"<b><i>{_inline(match.group('body'))}</i></b>")

    def bold(match: re.Match[str]) -> str:
        return keep(f"<b>{_inline(match.group('a') or match.group('b') or '')}</b>")

    def strike(match: re.Match[str]) -> str:
        return keep(f"<s>{_escape(match.group('body'))}</s>")

    def italic(match: re.Match[str]) -> str:
        return keep(f"<i>{_escape(match.group('body'))}</i>")

    working = _IMAGE_RE.sub(image, text)
    working = _LINK_RE.sub(link, working)
    working = _CODE_SPAN_RE.sub(code, working)
    working = _BOLD_ITALIC_RE.sub(bold_italic, working)
    working = _BOLD_RE.sub(bold, working)
    working = _STRIKE_RE.sub(strike, working)
    working = _ITALIC_RE.sub(italic, working)
    working = _escape(working)
    for index, html in enumerate(tokens):
        working = working.replace(f"\x00{index}\x00", html)
    return working


def _plain_inline(text: str) -> str:
    """Strip Markdown syntax so the plain-text twin reads like a document."""
    working = _IMAGE_RE.sub(lambda m: (m.group("alt").strip() or "صورة"), text)
    working = _LINK_RE.sub(lambda m: m.group("label").strip() or m.group("url"), working)
    working = _CODE_SPAN_RE.sub(lambda m: m.group("code").strip(), working)
    working = _BOLD_ITALIC_RE.sub(lambda m: m.group("body"), working)
    working = _BOLD_RE.sub(lambda m: m.group("a") or m.group("b") or "", working)
    working = _STRIKE_RE.sub(lambda m: m.group("body"), working)
    working = _ITALIC_RE.sub(lambda m: m.group("body"), working)
    return working.strip()


def _labelled(body: str) -> tuple[str, str] | None:
    """Bold a short leading label (``ما حدث:``) the way digests are read."""
    match = _LABEL_RE.match(body.strip())
    if match is None:
        return None
    label = _escape(match.group("label").strip())
    rest = _inline(match.group("rest"))
    plain_label = match.group("label").strip()
    return f"<b>{label}:</b> {rest}", f"{plain_label}: {_plain_inline(match.group('rest'))}"


def _bullet_block(line: str) -> _Block | None:
    match = _BULLET_RE.match(line)
    if match is not None:
        nested = len(match.group("indent")) >= 2
        glyph = "◦" if nested else "•"
        body = match.group("body")
        labelled = _labelled(body)
        if labelled is not None:
            return _Block(f"{glyph} {labelled[0]}", f"{glyph} {labelled[1]}", _LIST)
        return _Block(f"{glyph} {_inline(body)}", f"{glyph} {_plain_inline(body)}", _LIST)
    ordered = _ORDERED_RE.match(line)
    if ordered is not None:
        nested = len(ordered.group("indent")) >= 2
        prefix = "" if nested else f"{ordered.group('number')}. "
        body = ordered.group("body")
        labelled = _labelled(body)
        if labelled is not None:
            return _Block(f"{prefix}{labelled[0]}", f"{prefix}{labelled[1]}", _LIST)
        return _Block(f"{prefix}{_inline(body)}", f"{prefix}{_plain_inline(body)}", _LIST)
    return None


def _heading_block(line: str) -> _Block | None:
    match = _HEADING_RE.match(line)
    if match is None:
        return None
    level = len(match.group("marks"))
    title = match.group("title")
    if level <= 1:
        html = f"<b>{_inline(title)}</b>\n{DIVIDER}"
        return _Block(html, f"{_plain_inline(title)}\n{'─' * len(DIVIDER)}", _HEADING)
    marker = "▌" if level == 2 else "▸"
    return _Block(
        f"<b>{marker} {_inline(title)}</b>",
        f"{marker} {_plain_inline(title)}",
        _HEADING,
    )


def build_blocks(text: str) -> list[_Block]:
    """Render an answer into display blocks: headings, lists, quotes, code."""
    blocks: list[_Block] = []
    quote: list[str] = []
    fence: list[str] = []
    in_fence = False

    def add(block: _Block) -> None:
        if not block.html:
            return
        if blocks and blocks[-1].kind == block.kind and block.kind == _LIST:
            previous = blocks[-1]
            blocks[-1] = _Block(
                f"{previous.html}\n{block.html}",
                f"{previous.plain}\n{block.plain}",
                _LIST,
            )
            return
        if blocks and blocks[-1].kind == _PARAGRAPH and block.kind == _PARAGRAPH:
            previous = blocks[-1]
            blocks[-1] = _Block(
                f"{previous.html} {block.html}",
                f"{previous.plain} {block.plain}",
                _PARAGRAPH,
            )
            return
        blocks.append(block)

    def flush_quote() -> None:
        nonlocal quote
        if not quote:
            return
        body = "<br>".join(_escape(line) for line in quote)
        plain = " ".join(_plain_inline(line) for line in quote)
        add(_Block(f"<blockquote>{body}</blockquote>", f"│ {plain}"))
        quote = []

    def flush_fence() -> None:
        nonlocal fence, in_fence
        if not in_fence and not fence:
            return
        code = "\n".join(fence)
        fence = []
        in_fence = False
        if not code.strip():
            return
        add(_Block(f"<pre>{_escape(code)}</pre>", code, _CODE))

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if in_fence:
            if _FENCE_RE.match(line):
                flush_fence()
            else:
                fence.append(raw)
            continue
        if _FENCE_RE.match(line) and _RULE_RE.match(line) is None:
            flush_quote()
            in_fence = True
            fence = []
            continue
        if not line.strip():
            flush_quote()
            continue
        flush_quote()
        heading = _heading_block(line)
        if heading is not None:
            add(heading)
            continue
        if _RULE_RE.match(line) is not None:
            add(_Block(DIVIDER, DIVIDER, _DIVIDER))
            continue
        quoted = _QUOTE_RE.match(line)
        if quoted is not None:
            quote.append(quoted.group("body").strip())
            continue
        bullet = _bullet_block(line)
        if bullet is not None:
            add(bullet)
            continue
        stripped = line.strip()
        add(_Block(_inline(stripped), _plain_inline(stripped)))
    flush_quote()
    flush_fence()
    return blocks


def _join(blocks: list[_Block], *, html: bool) -> str:
    attribute = "html" if html else "plain"
    return "\n\n".join(getattr(block, attribute) for block in blocks).strip()


def _split_block(block: _Block, max_len: int) -> list[_Block]:
    """Cut one oversized block, keeping a code block wrapped in every piece."""
    if len(block.html) <= max_len:
        return [block]
    if block.html.startswith("<pre>") and block.html.endswith("</pre>"):
        code = block.html[len("<pre>") : -len("</pre>")]
        step = max(64, max_len - 11)
        pieces = [code[start : start + step] for start in range(0, len(code), step)]
        return [_Block(f"<pre>{piece}</pre>", piece, block.kind) for piece in pieces if piece]
    ratio = len(block.plain) / max(1, len(block.html))
    pieces: list[_Block] = []
    for index in range(0, len(block.html), max_len):
        html_piece = block.html[index : index + max_len]
        start = int(index * ratio)
        end = int((index + max_len) * ratio)
        pieces.append(_Block(html_piece, block.plain[start:end] or block.plain, block.kind))
    return pieces


def build_pages(text: str, *, max_len: int = MAX_PAGE_CHARS) -> list[Page]:
    """Render Markdown into Telegram-ready pages, never cutting a topic in half."""
    blocks: list[_Block] = []
    for block in build_blocks(text):
        blocks.extend(_split_block(block, max_len))
    pages: list[Page] = []
    current: list[_Block] = []
    size = 0
    for block in blocks:
        addition = len(block.html) + (2 if current else 0)
        # A heading starts a new page, except directly under a document title:
        # "# نشرة" followed by "## عاجل" is one page, not a lonely title page.
        starts_topic = (
            block.kind == _HEADING
            and bool(current)
            and any(item.kind != _HEADING for item in current)
        )
        if current and (starts_topic or size + addition > max_len):
            pages.append(_page(current))
            current = []
            size = 0
            addition = len(block.html)
        current.append(block)
        size += addition
    if current:
        pages.append(_page(current))
    return pages


def _unescape(value: str) -> str:
    return value.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _page_links(
    html: str, plain: str, *, limit: int = MAX_PAGE_LINKS
) -> tuple[tuple[str, str], ...]:
    """Read this page's sources from its rendered links, not the whole answer."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(html):
        url = _unescape(match.group("url")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        label = _unescape(match.group("label")).strip() or "افتح الصفحة"
        found.append((label[:80], url))
        if len(found) >= limit:
            break
    if not found:
        found = extract_links(plain, limit=limit)
    return tuple(found)


def _page(blocks: list[_Block], images: tuple[tuple[str, str], ...] = ()) -> Page:
    html = _join(blocks, html=True)
    plain = _join(blocks, html=False)
    return Page(
        html=html,
        plain=plain,
        links=_page_links(html, plain),
        images=images,
    )


def first_page(text: str, *, max_len: int = MAX_PAGE_CHARS) -> Page | None:
    """The single page that fits one message, or None for an empty answer."""
    pages = build_pages(text, max_len=max_len)
    return pages[0] if pages else None


def render_markdown_html(text: str, *, max_len: int = MAX_PAGE_CHARS) -> str:
    """Render the whole answer as Telegram HTML, page breaks kept as spacing.

    Delivery uses :func:`build_pages` instead; this is the one-shot rendering.
    """
    return "\n\n".join(page.html for page in build_pages(text, max_len=max_len)).strip()
