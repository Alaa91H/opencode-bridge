"""Completion sentinel and UTC-noon scheduling for durable multi-day agent jobs."""
from __future__ import annotations

from datetime import UTC, datetime

from bridge.domain.tasks.retry_policy import next_daily_reset_utc

COMPLETION_SENTINEL = "[[BRIDGE_TASK_COMPLETE]]"

# Arabic / English phrases that also mean "the original job is done" when the
# agent forgot the machine sentinel. Checked only as a fallback.
_COMPLETION_PHRASES = (
    "اكتملت المهمة",
    "تم إنجاز المهمة",
    "المهمة مكتملة",
    "تم الانتهاء من المهمة",
    "task is complete",
    "task completed",
    "job is complete",
    "job completed",
    "all done",
)

# Punctuation that may wrap a phrase or a sentinel tail without changing its
# meaning.
_TAIL_PUNCTUATION = " -–—:،,.\"'()[]؛"

# A hedged line never completes the original job, however short it is: a
# question asks whether the work is finished, it does not declare it, and a
# negation ("not yet", "لم تكتمل") is the opposite of a completion.
_HEDGE_MARKERS = (
    "?",
    "؟",
    "not ",
    "n't",
    "غير",
    "لم ",
    "ليست",
    "ليس",
    "جزئي",
    "مؤقت",
    "ما زال",
)

# A done phrase may be followed by a short sign-off, mirroring the sentinel rule.
# The budget is deliberately small: a false negative costs the owner one more
# day, while a false positive would end a long job that is not actually finished.
_MAX_PHRASE_TAIL = 24


def _hedged(text: str) -> bool:
    return any(marker in text for marker in _HEDGE_MARKERS)


def _phrase_completes(line: str) -> bool:
    """True when the line *is* a done phrase, not merely mentions one."""
    core = line.casefold().strip(_TAIL_PUNCTUATION)
    if not core or _hedged(core):
        return False
    for phrase in _COMPLETION_PHRASES:
        if core == phrase:
            return True
        if core.startswith(phrase) and len(core) <= len(phrase) + _MAX_PHRASE_TAIL:
            return True
    return False


def is_task_complete(text: str) -> bool:
    """Only a standalone trailing sentinel marks the complete ORIGINAL job.

    Rules, from strongest to weakest signal:

    * a trailing line equal to the sentinel completes the job;
    * the sentinel followed by a short, unhedged sign-off completes it too,
      while a prose mention ("Example: [...] is the marker") does not;
    * a final done phrase completes it only when the line *is* that phrase,
      optionally with a short sign-off, and never when the line hedges or asks.

    The bias is deliberate: a missed completion costs the owner one more day,
    while a false one silently discards weeks of multi-day work.
    """
    if not text or not text.strip():
        return False
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return False
    last = lines[-1]
    if last == COMPLETION_SENTINEL:
        return True
    if COMPLETION_SENTINEL in last:
        raw_tail = last.split(COMPLETION_SENTINEL, 1)[1]
        if not raw_tail.strip():
            return True
        # "[[...]]؟" asks; it never declares.
        if _hedged(raw_tail):
            return False
        tail = raw_tail.strip(_TAIL_PUNCTUATION)
        if not tail or len(tail) <= 8:
            return True
        return _phrase_completes(tail)
    return _phrase_completes(last)


def continuation_delay_seconds(now: datetime | None = None) -> float:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    return max(1.0, (next_daily_reset_utc(reference) - reference).total_seconds())
