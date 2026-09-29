"""Telegram delivery adapter for framework-independent task execution."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from telegram.constants import ChatAction

from messages import empty_response_message
from progress_reporter import LiveProgressReporter


class TelegramExecutionDelivery:
    def __init__(
        self,
        bot: Any,
        repository: Any,
        progress_store: Any,
        live_reporters: dict[int, LiveProgressReporter],
        *,
        max_message_length: int,
        error_message: Any,
        display_preferences: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.progress_store = progress_store
        self.live_reporters = live_reporters
        self.max_message_length = max_message_length
        self.error_message = error_message
        self.display_preferences = display_preferences
        self.log = logger or logging.getLogger(__name__)
        self._typing_tasks: dict[int, tuple[asyncio.Event, asyncio.Task[None]]] = {}

    async def _typing_loop(
        self,
        chat_id: int | None,
        stop_event: asyncio.Event,
    ) -> None:
        try:
            while not stop_event.is_set() and chat_id:
                await self.bot.send_chat_action(
                    chat_id=chat_id,
                    action=ChatAction.TYPING,
                )
                await asyncio.sleep(4)
        except Exception as exc:
            self.log.debug("تعذر تحديث مؤشر الكتابة: %s", exc)

    async def begin(self, task: Any) -> LiveProgressReporter:
        owner_id = str(getattr(task, "owner_id", ""))
        reporter = LiveProgressReporter(
            task,
            self.bot,
            self.repository,
            self.progress_store,
            display_preferences=(
                (lambda: self.display_preferences(owner_id)) if self.display_preferences else None
            ),
        )
        self.live_reporters[task.id] = reporter
        await reporter.start()
        stop_event = asyncio.Event()
        typing_task = asyncio.create_task(
            self._typing_loop(task.chat_id, stop_event)
        )
        self._typing_tasks[task.id] = (stop_event, typing_task)
        return reporter

    async def send_outputs(self, task: Any, paths: list[Any]) -> int:
        sent = 0
        for path in paths:
            with path.open("rb") as handle:
                await self.bot.send_document(
                    chat_id=task.chat_id,
                    document=handle,
                    filename=path.name,
                    caption="ملف ناتج عن الطلب" if sent == 0 else None,
                )
            sent += 1
        return sent

    def final_text(self, text: str) -> str:
        body = text.strip() or empty_response_message()
        if len(body) > self.max_message_length:
            suffix = "\n\n… تم اختصار الرد بسبب حد طول رسالة Telegram."
            body = body[: max(1, self.max_message_length - len(suffix))].rstrip() + suffix
        return body

    def error_text(self, exc: Exception, operation: str) -> str:
        return self.error_message(exc, operation)

    async def end(self, task: Any) -> None:
        state = self._typing_tasks.pop(task.id, None)
        if state is not None:
            stop_event, typing_task = state
            stop_event.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
        self.live_reporters.pop(task.id, None)
