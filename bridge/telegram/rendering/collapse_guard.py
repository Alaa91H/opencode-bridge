"""Never deliver a collapsed answer: a repetition loop is not an answer.

A model that degenerates does not fail loudly. It emits a few good paragraphs
and then loops — `wwwwww…`, `products_html_documents_allocate…` — for the rest of
its budget. Delivering that as fifteen Telegram pages is worse than delivering
nothing: the operator reads pages of noise and concludes the bridge is broken.

This module finds where such a collapse starts, so the delivery can cut the
answer at the last coherent sentence, say plainly that the output broke down,
and hand over the complete raw text as a file instead of pretending the
repetition was content. A loop has to be long enough to be certain: a code block
that legitimately repeats, or a table row that repeats a character, must survive.
"""

from __future__ import annotations

import re

# A single character repeated this many times is a stuck decoder, not writing.
_CHAR_RUN = re.compile(r"(?P<char>[^\s\w]|\w)\1{59,}")
# The same short phrase, back to back, at least this many times.
_PHRASE_LOOP = re.compile(r"(?P<phrase>[^\s]{4,120}?)(?:\s*\1){4,}", re.DOTALL)
# A repeated line, which is how a loop usually looks once prose gives out.
_REPEATED_LINE = re.compile(r"(?P<line>[^\n]{6,120})\n(?:\s*\1\n?){4,}")

TAIL_NOTE = (
    "⚠️ **توقف الرد هنا** — انقطع الرد في منتصفه بسبب خلل في مخرجات النموذج "
    "(تكرار متصل)، وما سبق هو الجزء المفهوم منه. النسخة الكاملة "
    "بما فيها هذا الجزء في الملف المرفق."
)


def _clean_cut(text: str, index: int) -> int:
    """Move a cut back to the last coherent place to stop reading."""
    if index <= 0:
        return 0
    head = text[:index]
    for boundary in ("\n\n", "\n", ". ", "؟", "! ", "، "):
        position = head.rfind(boundary)
        if position > 0:
            return position + len(boundary)
    return index


def collapse_start(text: str) -> int | None:
    """Offset where a repetition collapse begins, or None when the answer is clean."""
    if not text:
        return None
    positions = [
        match.start()
        for match in (
            _CHAR_RUN.search(text),
            _PHRASE_LOOP.search(text),
            _REPEATED_LINE.search(text),
        )
        if match is not None
    ]
    if not positions:
        return None
    return _clean_cut(text, min(positions))


def is_degenerate(text: str) -> bool:
    """True when the answer collapsed into repetition anywhere in its text."""
    return collapse_start(text) is not None


def truncated_answer(text: str) -> tuple[str, bool]:
    """The readable part of an answer, and whether anything was cut.

    The tail note is only added when something real was removed: an answer that
    was already cut short stays exactly as the agent wrote it.
    """
    index = collapse_start(text or "")
    if index is None:
        return text or "", False
    kept = (text or "")[:index].rstrip()
    if not kept:
        return text or "", False
    return f"{kept}\n\n{TAIL_NOTE}", True
