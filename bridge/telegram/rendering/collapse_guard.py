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
# The scan is line-based below: an equivalent regular expression with a
# backreference tried every offset of a long single-line answer and spent
# minutes inside re.search before it could fail.
_REPEATED_LINE_WORD = re.compile(r"\w")
_DECORATIVE_RULE = re.compile(r"^\s*[-=_*#~]{3,}\s*$")
MIN_LOOP_CHARS = 200
MIN_PHRASE_CHARS = 5
MAX_PHRASE_CHARS = 60
MIN_PHRASE_REPEATS = 5

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


def _inline_phrase_loop(scanned: str) -> int | None:
    """Offset where a multi-word phrase starts repeating inside a single line.

    The patterns above see either one token or a whole line. The commonest shape
    of a collapse is neither: the same two or three words repeated inline,
    separated by spaces, along one long line. ``\\S*`` cannot span a space, so
    that shape slipped through every rule, and a whole-line pattern cannot see it
    because the repetition never reaches a newline.

    The search is a plain period scan rather than a regex, because a pattern that
    spans arbitrary words is exactly where backtracking turns a four-thousand
    character page into a stalled delivery. The ``find`` prefilter keeps the
    scan linear in the line length: a five-character anchor can only be a
    repeated unit if it reappears within the maximum phrase length, and the
    common clean line has no such occurrence, so the inner verifier almost
    never runs.
    """
    anchor_len = MIN_PHRASE_CHARS
    for line_match in re.finditer(r"[^\n]+", scanned):
        line = line_match.group(0)
        length = len(line)
        if length < MIN_LOOP_CHARS:
            continue
        largest = min(MAX_PHRASE_CHARS, length // MIN_PHRASE_REPEATS)
        index = 0
        while index + 2 * anchor_len <= length:
            anchor = line[index:index + anchor_len]
            if not anchor.strip() or not any(ch.isalnum() for ch in anchor):
                index += 1
                continue
            search_end = min(index + largest, length - anchor_len) + anchor_len
            candidate = line.find(anchor, index + anchor_len, search_end)
            while candidate != -1:
                period = candidate - index
                chunk = line[index:index + period]
                repeats = 1
                cursor = index + period
                while line.startswith(chunk, cursor):
                    repeats += 1
                    cursor += period
                    if repeats >= MIN_PHRASE_REPEATS and repeats * period >= MIN_LOOP_CHARS:
                        return line_match.start() + index
                candidate = line.find(anchor, candidate + 1, search_end)
            index += 1
    return None


def _repeated_line_start(text: str) -> int | None:
    """Offset of the first line in a repeated-line run, or ``None``.

    Only whole lines can be the repeated unit this rule is meant to catch, so
    the scan walks lines and compares stripped text. The regex it replaces had
    to try every offset of the input against a backreference: on a single
    200k-character answer that turned delivery into minutes of CPU inside
    ``re.search``. A blank line is whitespace between repeats, which the old
    pattern allowed, so it does not break a run.
    """
    offset = 0
    previous: str | None = None
    run_start = 0
    run_count = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\n")
        key = line.strip()
        if 21 <= len(key) <= 321 and _REPEATED_LINE_WORD.search(key):
            if key == previous:
                run_count += 1
            else:
                previous = key
                run_count = 1
                run_start = offset + (len(line) - len(line.lstrip()))
            if run_count >= 5:
                return run_start
        elif key:
            previous = None
            run_count = 0
        offset += len(raw)
    return None


def collapse_start(text: str) -> int | None:
    """Offset where a repetition collapse begins, or None when the answer is clean."""
    if not text:
        return None
    scanned = _mask_decoration(text)
    positions: list[int] = []
    char_run = _CHAR_RUN.search(scanned)
    if char_run is not None:
        positions.append(char_run.start())
    repeated_line = _repeated_line_start(scanned)
    if repeated_line is not None:
        positions.append(repeated_line)
    phrase = _PHRASE_LOOP.search(scanned)
    if phrase is not None and len(phrase.group(0)) >= MIN_LOOP_CHARS:
        positions.append(phrase.start())
    inline = _inline_phrase_loop(scanned)
    if inline is not None:
        positions.append(inline)
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
