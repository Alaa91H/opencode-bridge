"""Completion sentinel and UTC-noon scheduling for durable multi-day agent jobs."""
from __future__ import annotations

from datetime import UTC, datetime

from bridge.domain.tasks.retry_policy import next_daily_reset_utc

COMPLETION_SENTINEL = "[[BRIDGE_TASK_COMPLETE]]"


def is_task_complete(text: str) -> bool:
    """Only a standalone trailing sentinel marks the complete ORIGINAL job."""
    return bool(text and text.rstrip().splitlines()[-1].strip() == COMPLETION_SENTINEL)


def continuation_delay_seconds(now: datetime | None = None) -> float:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    return max(1.0, (next_daily_reset_utc(reference) - reference).total_seconds())
