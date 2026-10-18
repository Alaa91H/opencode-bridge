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

# A run of a single WORD character is a decoder that is stuck. Punctuation runs
# are decoration: the 70 hyphens a unittest prints between results, a markdown
# rule, a table border. Cutting a correct answer because it contained a rule is
# worse than the collapse this guard exists to stop, so decoration is masked out
# before anything is measured.
_CHAR_RUN = re.compile(r"(?P<char>\w)\1{119,}")
# The same short phrase back to back; it must be real words and long enough that
# ordinary prose cannot match it.
_PHRASE_LOOP = re.compile(r"(?P<phrase>\S*\w\S{4,119}?)(?:\s*\1){4,}")
# The same content line repeated, which is how a loop looks once prose gives out.
_REPEATED_LINE = re.compile(r"(?P<line>[^\n]{20,160}?\w[^\n]{0,160}?)\n(?:\s*\1\n?){4,}")
_DECORATIVE_RULE = re.compile(r"^\s*[-=_*#~]{3,}\s*$")
MIN_LOOP_CHARS = 200

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


def _mask_decoration(text: str) -> str:
    """Blank decorative rules in place, so offsets stay meaningful.

    Blank lines of ``-``, ``=``, ``*``, ``_``, ``#`` or ``~`` are layout: a
    unittest separator, a markdown rule, a table border. They are replaced with
    spaces of the same length, which keeps every later offset pointing at the
    same place in the original answer.
    """
    return "\n".join(
        " " * len(line) if _DECORATIVE_RULE.match(line) else line
        for line in text.splitlines()
    )


def collapse_start(text: str) -> int | None:
    """Offset where a repetition collapse begins, or None when the answer is clean."""
    if not text:
        return None
    scanned = _mask_decoration(text)
    positions: list[int] = []
    for match in (_CHAR_RUN.search(scanned), _REPEATED_LINE.search(scanned)):
        if match is not None:
            positions.append(match.start())
    phrase = _PHRASE_LOOP.search(scanned)
    if phrase is not None and len(phrase.group(0)) >= MIN_LOOP_CHARS:
        positions.append(phrase.start())
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
