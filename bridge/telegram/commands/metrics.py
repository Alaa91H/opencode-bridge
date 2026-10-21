"""Thin Telegram adapter for operational metrics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.telegram.rendering.metrics import render_metrics

Reply = Callable[[Any, str], Awaitable[None]]

MAX_WINDOW_HOURS = 24 * 30


def parse_window_hours(argument: str | None) -> int | None:
    """Read an optional `/metrics [hours]` window, clamped to a sane range.

    Returns None for absent or unusable input so the caller keeps the default
    window; a wrong argument must never fail the command.
    """
    if argument is None:
        return None
    token = argument.strip().split()[0] if argument.strip() else ""
    if not token:
        return None
    try:
        hours = int(token)
    except ValueError:
        return None
    return max(1, min(hours, MAX_WINDOW_HOURS))


def window_argument(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Extract the window argument from ``context.args``.

    ``context.args`` is the single source of truth: the control panel dispatches
    the same handlers with ``context.args`` set to the button payload while
    ``update.message`` is the callback's own message. Parsing the message text
    instead would read an unrelated word from an arbitrary caption.
    """
    args = list(getattr(context, "args", None) or [])
    if args:
        return str(args[0])
    text = getattr(update.message, "text", None) if update.message else None
    if isinstance(text, str) and text.strip().startswith("/metrics"):
        remainder = text.strip()[len("/metrics"):].strip()
        return remainder or None
    return None


class MetricsCommands:
    def __init__(
        self,
        service: Any,
        *,
        reply: Reply,
        error_message: Callable[[Exception, str], str] | None = None,
    ) -> None:
        self.service = service
        self.reply = reply
        self.error_message = error_message

    async def metrics(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if not message:
            return
        hours = parse_window_hours(window_argument(update, context))
        try:
            snapshot = await self.service.snapshot(
                **({"window_hours": hours} if hours is not None else {})
            )
        except Exception as exc:
            # Never fail silently: a silent command looks like an idle bridge.
            text = (
                self.error_message(exc, "قراءة القياسات")
                if self.error_message is not None
                else "تعذّر قراءة القياسات الآن. حاول مرة أخرى."
            )
            await self.reply(message, text)
            return
        await self.reply(message, render_metrics(snapshot))
