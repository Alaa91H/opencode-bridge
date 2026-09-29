"""Telegram rendering and persistence for safe live agent progress."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opencode_client import OpenCodeClient
from progress import ProgressStore, render_progress, serialize_progress, summarize_agent_event
from task_queue import QueuedTask, TaskQueueStore

log = logging.getLogger("opencode_bridge.progress")
UPDATE_INTERVAL_SECONDS = 2.5


@dataclass(frozen=True)
class DisplayPreferences:
    """The owner's live display choices, read once per render.

    ``notification_level`` decides whether live progress is edited into the
    status message and ``output_style`` decides how much detail it carries. The
    phase-aware decision lives in ``LiveProgressReporter._show_progress``; this
    object only carries the values.
    """

    notification_level: str = "normal"
    output_style: str = "summary"

    @property
    def detail(self) -> bool:
        return self.output_style == "full"


DEFAULT_DISPLAY = DisplayPreferences()


class LiveProgressReporter:
    """Show sanitized operational milestones without exposing raw model reasoning."""

    def __init__(
        self,
        task: QueuedTask,
        bot: Any,
        queue: TaskQueueStore,
        progress_store: ProgressStore,
        display_preferences: Callable[[], "DisplayPreferences"] | None = None,
    ) -> None:
        self.task = task
        self.bot = bot
        self.queue = queue
        self.progress_store = progress_store
        self.progress = progress_store.start(task.id, task.owner_id, task.chat_id)
        self._display = display_preferences
        self._last_rendered = 0.0
        self._last_message = ""

    async def _preferences(self) -> DisplayPreferences:
        if self._display is None:
            return DEFAULT_DISPLAY
        try:
            value = self._display()
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            log.info("تعذر قراءة تفضيلات العرض: %s", type(exc).__name__)
            return DEFAULT_DISPLAY
        return value if isinstance(value, DisplayPreferences) else DEFAULT_DISPLAY

    async def _show_progress(self, phase: str | None = None) -> bool:
        """Honor the owner's notification level for live progress updates."""
        level = (await self._preferences()).notification_level
        if level == "silent":
            return False
        if level == "errors":
            return (phase or self.progress.phase) == "failed"
        return True

    async def _detail(self) -> bool:
        return (await self._preferences()).detail

    async def start(self) -> None:
        await self._persist()
        text = render_progress(self.progress, detail=await self._detail())
        try:
            if self.task.status_message_id is not None:
                self.progress_store.set_message_id(self.task.id, int(self.task.status_message_id))
                await self.bot.edit_message_text(
                    chat_id=self.task.chat_id,
                    message_id=int(self.task.status_message_id),
                    text=text,
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
            else:
                message = await self.bot.send_message(
                    chat_id=self.task.chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
                message_id = int(message.message_id)
                self.progress_store.set_message_id(self.task.id, message_id)
                await self.queue.set_status_message_id(self.task.id, message_id)
            self._last_message = text
            self._last_rendered = time.monotonic()
        except Exception as exc:
            log.warning("تعذر تجهيز رسالة التقدم للمهمة %s: %s", self.task.id, exc)

    async def record(self, phase: str, message: str, kind: str = "info", force: bool = False) -> None:
        previous = self.progress.entries[-1] if self.progress.entries else None
        if previous and previous.phase == phase and previous.message == message:
            return
        self.progress_store.record(self.task.id, phase, message, kind)
        await self._persist()
        await self.refresh(force=force)

    async def consume_events(self, client: OpenCodeClient, session_id: str) -> None:
        try:
            async for event in client.stream_events(session_id):
                summary = summarize_agent_event(event)
                if summary is None:
                    continue
                phase, message, kind = summary
                await self.record(phase, message, kind)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Progress visibility is additive; loss of the stream must never stop the task.
            log.info("توقف بث تقدم المهمة %s: %s", self.task.id, type(exc).__name__)

    async def refresh(self, force: bool = False, detail: bool = False, final: bool = False) -> None:
        if self.progress.message_id is None:
            return
        if not final and not await self._show_progress():
            return
        now = time.monotonic()
        if not force and now - self._last_rendered < UPDATE_INTERVAL_SECONDS:
            return
        text = render_progress(self.progress, detail=detail or await self._detail())
        if text == self._last_message and not force:
            return
        try:
            await self.bot.edit_message_text(
                chat_id=self.task.chat_id,
                message_id=self.progress.message_id,
                text=text,
                reply_markup=None,
                disable_web_page_preview=True,
            )
            self._last_message = text
            self._last_rendered = now
        except Exception as exc:
            log.debug("تعذر تحديث تقدم المهمة %s: %s", self.task.id, exc)

    async def finish(self, status: str, message: str, kind: str = "success") -> None:
        self.progress_store.finish(self.task.id, status, message, kind)
        await self._persist()
        await self.refresh(force=True, detail=False, final=True)

    async def finalize_text(self, text: str, status: str = "completed", message: str = "اكتمل التنفيذ.") -> None:
        """Replace the live status message with the final user-facing answer."""
        self.progress_store.finish(self.task.id, status, message, "success" if status == "completed" else "error")
        await self._persist()
        message_id = self.progress.message_id or self.task.status_message_id
        if message_id is None:
            try:
                sent = await self.bot.send_message(
                    chat_id=self.task.chat_id,
                    text=text,
                    disable_web_page_preview=True,
                )
                message_id = int(sent.message_id)
                self.progress_store.set_message_id(self.task.id, message_id)
                await self.queue.set_status_message_id(self.task.id, message_id)
            except Exception as exc:
                log.warning("تعذر إرسال النتيجة النهائية للمهمة %s: %s", self.task.id, exc)
            return
        try:
            await self.bot.edit_message_text(
                chat_id=self.task.chat_id,
                message_id=int(message_id),
                text=text,
                reply_markup=None,
                disable_web_page_preview=True,
            )
            self._last_message = text
            self._last_rendered = time.monotonic()
        except Exception as exc:
            log.warning("تعذر تحديث رسالة النتيجة النهائية للمهمة %s: %s", self.task.id, exc)

    async def _persist(self) -> None:
        await self.queue.update_activity(self.task.id, serialize_progress(self.progress))
