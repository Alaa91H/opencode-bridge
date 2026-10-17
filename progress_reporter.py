"""Telegram rendering and persistence for safe live agent progress."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bridge.config.runtime_flags import enabled_lenient as runtime_flag_enabled
from bridge.domain.tasks.live_output import AgentLiveAssembler, redact_output
from opencode_client import OpenCodeClient
from progress import ProgressStore, render_progress, serialize_progress, summarize_agent_event
from task_queue import QueuedTask, TaskQueueStore

log = logging.getLogger("opencode_bridge.progress")
UPDATE_INTERVAL_SECONDS = 2.5
# Coalescing window for the persisted activity trail. Progress is rendered from
# memory, so bounding the database writes changes no owner-visible behaviour.
PERSIST_INTERVAL_SECONDS = 5.0


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
        display_preferences: Callable[[], DisplayPreferences] | None = None,
    ) -> None:
        self.task = task
        self.bot = bot
        self.queue = queue
        self.progress_store = progress_store
        self.progress = progress_store.start(task.id, task.owner_id, task.chat_id)
        self._display = display_preferences
        self._last_rendered = 0.0
        self._last_message = ""
        self._live_enabled = runtime_flag_enabled("AGENT_LIVE_OUTPUT", default=False)
        # Read once per task, not per tick: enabled() can fall back to parsing
        # .env, and a preview update runs every 1.5s for the whole task.
        self._stream_drafts = runtime_flag_enabled("TELEGRAM_STREAM_DRAFTS", default=False)
        self._live_parser = AgentLiveAssembler()
        self._live_buffer = ""
        self._live_message_text = ""
        self._live_last_sent = 0.0
        self._last_persisted = 0.0
        self._persist_pending = False

    async def _preferences(self) -> DisplayPreferences:
        """Read the owner's display choices, never failing a task over them.

        Falling back is correct: a preferences read must not be able to fail a
        task. But silently swallowing the reason is how a missing import in
        ``bot.py`` went unnoticed while every task ignored the owner's saved
        notification level. The warning carries the exception itself so the
        failure is diagnosable from the journal.
        """
        if self._display is None:
            return DEFAULT_DISPLAY
        try:
            value = self._display()
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            log.warning(
                "تعذر قراءة تفضيلات العرض، سيتم استخدام الافتراضي: %s: %s",
                type(exc).__name__,
                exc,
            )
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
        await self._persist(force=True)
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

    async def _publish_live(self, *, flush: bool = False) -> None:
        """Edit the existing status message; never create a separate tool log."""
        if not self._live_enabled or not self._live_buffer:
            return
        if not await self._show_progress() or self.progress.message_id is None:
            self._live_buffer = ""
            return
        now = time.monotonic()
        if not flush and now - self._live_last_sent < 1.5 and len(self._live_buffer) < 2600:
            return
        # Keep only a short user-facing preview. Full text arrives once, in the
        # same status message, from finalize_text().
        pending = (self._live_message_text + self._live_buffer)[-2900:]
        prefix = "عم بكتب الجواب…\n\n"
        updated = prefix + redact_output(pending)
        if updated == self._last_message:
            self._live_buffer = ""
            return
        if self._stream_drafts:
            # Experimental animated drafts (private chats): same preview text
            # through sendMessageDraft; any failure falls back to the edit
            # below, so groups and old servers keep working unchanged.
            try:
                from bridge.telegram.rich_delivery import send_stream_draft

                if await send_stream_draft(
                    self.bot, self.task.chat_id, int(self.task.id), updated
                ):
                    self._live_buffer = ""
                    self._live_message_text = pending
                    self._live_last_sent = now
                    self._last_message = updated
                    self._last_rendered = now
                    return
            except Exception as exc:
                log.debug("تعذر بث المسودة للمهمة %s: %s", self.task.id, type(exc).__name__)
        try:
            await self.bot.edit_message_text(
                chat_id=self.task.chat_id,
                message_id=self.progress.message_id,
                text=updated,
                reply_markup=None,
                disable_web_page_preview=True,
            )
            self._live_buffer = ""
            self._live_message_text = pending
            self._live_last_sent = now
            self._last_message = updated
            self._last_rendered = now
        except Exception as exc:
            # Previews must never fail an actual task or leak into new messages.
            log.warning("تعذر تحديث معاينة الرد للمهمة %s: %s", self.task.id, type(exc).__name__)

    async def consume_events(self, client: OpenCodeClient, session_id: str) -> None:
        try:
            async for event in client.stream_events(session_id):
                if self._live_enabled:
                    chunk = self._live_parser.consume(event)
                    if chunk:
                        self._live_buffer += chunk
                        if len(self._live_buffer) > 20000:
                            # Bounded backlog if Telegram is throttling or offline.
                            self._live_buffer = (
                                "\n[تنبيه: تم تجاوز حد انتظار البث وفُقد بعض النص السابق]\n"
                                + self._live_buffer[-15000:]
                            )
                        await self._publish_live()
                    if event.get("type") == "session.idle":
                        await self._publish_live(flush=True)
                summary = summarize_agent_event(event)
                if summary is not None:
                    phase, message, kind = summary
                    await self.record(phase, message, kind)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("توقف بث تقدم المهمة %s: %s", self.task.id, type(exc).__name__)
        finally:
            await self._publish_live(flush=True)
            # The stream ended: this is the last chance to persist the trail
            # before the task moves on to its terminal transition.
            await self._persist(force=True)

    async def refresh(self, force: bool = False, detail: bool = False, final: bool = False) -> None:
        if self.progress.message_id is None:
            return
        if not final and not await self._show_progress():
            return
        if not final and self._live_message_text:
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
        await self._persist(force=True)
        await self.refresh(force=True, detail=False, final=True)

    async def finalize_text(self, text: str, status: str = "completed", message: str = "اكتمل التنفيذ.") -> None:
        """Replace the live status message with the final user-facing answer.

        The answer arrives as Markdown, and Telegram renders none of it: `##`,
        `**bold**` and `-` bullets would show verbatim. Rendering here is what
        makes the message read like a document, and a client that rejects HTML
        still gets the same page as plain text.
        """
        from bridge.telegram.rendering.markdown_html import MAX_PAGE_CHARS, first_page

        page = first_page(text, max_len=MAX_PAGE_CHARS)
        rendered = page.html if page is not None else text
        plain = page.plain if page is not None else text
        self.progress_store.finish(self.task.id, status, message, "success" if status == "completed" else "error")
        await self._persist(force=True)
        message_id = self.progress.message_id or self.task.status_message_id

        async def publish(use_html: bool) -> Any:
            kwargs: dict[str, Any] = {
                "chat_id": self.task.chat_id,
                "text": rendered if use_html else plain,
                "disable_web_page_preview": True,
            }
            if use_html:
                kwargs["parse_mode"] = "HTML"
            return await self.bot.send_message(**kwargs)

        async def publish_edit(target: int, use_html: bool) -> Any:
            kwargs: dict[str, Any] = {
                "chat_id": self.task.chat_id,
                "message_id": int(target),
                "text": rendered if use_html else plain,
                "reply_markup": None,
                "disable_web_page_preview": True,
            }
            if use_html:
                kwargs["parse_mode"] = "HTML"
            return await self.bot.edit_message_text(**kwargs)

        if message_id is None:
            try:
                sent = await publish(True)
            except Exception:
                try:
                    sent = await publish(False)
                except Exception as exc:
                    log.warning("تعذر إرسال النتيجة النهائية للمهمة %s: %s", self.task.id, exc)
                    return
            self.progress_store.set_message_id(self.task.id, int(sent.message_id))
            await self.queue.set_status_message_id(self.task.id, int(sent.message_id))
            return
        try:
            await publish_edit(int(message_id), True)
            self._last_message = rendered
            self._last_rendered = time.monotonic()
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return
            log.warning("تعذر تعديل رسالة النتيجة النهائية للمهمة %s: %s", self.task.id, type(exc).__name__)
            try:
                await publish_edit(int(message_id), False)
                self._last_message = plain
                self._last_rendered = time.monotonic()
                return
            except Exception:
                log.debug("تعذر تعديل الرسالة كنص عادي", exc_info=True)
            # A failed edit must not leave the user with a permanent progress
            # message. Send a new final answer only as a last-resort fallback.
            try:
                sent = await publish(True)
            except Exception:
                try:
                    sent = await publish(False)
                except Exception as send_exc:
                    log.warning("تعذر تسليم الرد النهائي للمهمة %s: %s", self.task.id, type(send_exc).__name__)
                    return
            self.progress_store.set_message_id(self.task.id, int(sent.message_id))
            await self.queue.set_status_message_id(self.task.id, int(sent.message_id))
            self._last_message = rendered
            self._last_rendered = time.monotonic()

    async def _persist(self, *, force: bool = False) -> None:
        """Write the activity trail, coalescing bursts.

        Every progress event used to become its own UPDATE plus a WAL commit on
        the one shared SQLite connection, taken under the queue lock. A
        tool-heavy task emits dozens of events, so those commits both burned I/O
        and delayed the worker's next claim behind the same lock.

        The in-memory store stays authoritative and is what `/progress` renders,
        so coalescing only affects the persisted copy. Terminal transitions and
        the end of the event stream force a write, so a finished task always
        keeps its full trail.
        """
        now = time.monotonic()
        if not force and now - self._last_persisted < PERSIST_INTERVAL_SECONDS:
            self._persist_pending = True
            return
        self._last_persisted = now
        self._persist_pending = False
        await self.queue.update_activity(self.task.id, serialize_progress(self.progress))
