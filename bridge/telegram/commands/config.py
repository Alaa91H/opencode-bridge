"""Thin Telegram commands for effective configuration visibility."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.domain.tasks.retry_policy import next_daily_reset_utc
from bridge.telegram.rendering.config import render_config, render_limits

Reply = Callable[[Any, str], Awaitable[None]]
QuotaPauseReader = Callable[[], Awaitable[datetime | None]]


def quota_status_lines(
    *,
    now: datetime | None = None,
    reset_at: datetime | None = None,
    paused_until: datetime | None = None,
) -> list[str]:
    """Human-readable free-quota reset lines for `/limits`.

    Pure helper (unit-testable): shows the daily reset countdown always, and
    the live exhaustion state only while a recorded pause is still in force.
    Observability only — it never changes quota enforcement.
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    reset = reset_at or next_daily_reset_utc(current)
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=UTC)
    delta = max(0.0, (reset - current).total_seconds())
    hours, remainder = divmod(int(delta), 3600)
    lines = [
        f"• تجديد الحصة المجانية اليومي: {reset:%H:%M} UTC (بعد {hours}h {remainder // 60}m)"
    ]
    if paused_until is not None:
        paused = paused_until if paused_until.tzinfo else paused_until.replace(tzinfo=UTC)
        if paused > current:
            lines.append(
                f"• الحصة مستنفدة حاليًا — الاستئناف التلقائي عند {paused:%H:%M} UTC"
            )
    return lines


class ConfigCommands:
    def __init__(
        self,
        service: Any,
        *,
        reply: Reply,
        quota_pause_reader: QuotaPauseReader | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.quota_pause_reader = quota_pause_reader

    @staticmethod
    def _owner(update: Update) -> str | None:
        return str(update.effective_user.id) if update.effective_user else None

    async def config(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = self.service.public_config(self._owner(update))
        await self.reply(update.message, render_config(payload))

    async def limits(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        payload = self.service.effective_limits(self._owner(update))
        text = render_limits(payload)
        try:
            paused = await self.quota_pause_reader() if self.quota_pause_reader else None
        except Exception:
            paused = None
        try:
            extra = quota_status_lines(
                paused_until=paused if isinstance(paused, datetime) else None
            )
            text = text + "\n" + "\n".join(extra)
        except Exception:
            pass
        await self.reply(update.message, text)
