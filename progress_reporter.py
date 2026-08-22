"""Telegram rendering and persistence for safe live agent progress."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from opencode_client import OpenCodeClient
from progress import ProgressStore, render_progress, serialize_progress, summarize_agent_event
from task_queue import QueuedTask, TaskQueueStore

log = logging.getLogger("opencode_bridge.progress")
UPDATE_INTERVAL_SECONDS = 2.5


class LiveProgressReporter:
    """Show sanitized operational milestones without exposing raw model reasoning."""

    def __init__(
        self,
        task: QueuedTask,
        bot: Any,
        queue: TaskQueueStore,
        progress_store: ProgressStore,
    ) -> None:
        self.task = task
        self.bot = bot
        self.queue = queue
        self.progress_store = progress_store
        self.progress = progress_store.start(task.id, task.owner_id, task.chat_id)
        self._last_rendered = 0.0
        self._last_message = ""

    async def start(self) -> None:
        await self._persist()
        try:
            message = await self.bot.send_message(
                chat_id=self.task.chat_id,
                text=render_progress(self.progress),
                disable_web_page_preview=True,
            )
            self.progress_store.set_message_id(self.task.id, int(message.message_id))
            self._last_message = render_progress(self.progress)
            self._last_rendered = time.monotonic()
        except Exception as exc:
            log.warning("تعذر إنشاء رسالة تقدم للمهمة %s: %s", self.task.id, exc)

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
        now = time.monotonic()
        if not force and now - self._last_rendered < UPDATE_INTERVAL_SECONDS:
            return
        text = render_progress(self.progress, detail=detail)
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

    async def _persist(self) -> None:
        await self.queue.update_activity(self.task.id, serialize_progress(self.progress))
