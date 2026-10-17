"""Telegram delivery adapter for framework-independent task execution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from telegram.constants import ChatAction

from messages import empty_response_message
from progress_reporter import LiveProgressReporter


class _DownloadLike:
    """Minimal DownloadRecord shape for reusing deliver_download with outputs."""

    def __init__(self, *, path: str, size_bytes: int, filename: str, direct_send_limit: int) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.filename = filename
        self.direct_send_limit = direct_send_limit


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
        from bridge.services.mirrors import MirrorService
        from bridge.telegram.download_delivery import deliver_download

        sent = 0
        pending_mirrors: list[str] = []
        for path in paths:
            item = Path(str(path))
            try:
                size = item.stat().st_size if item.is_file() else 0
            except OSError:
                continue
            # Small files: direct Telegram document (unchanged behaviour).
            if size <= 48 * 1024 * 1024:
                try:
                    with item.open("rb") as handle:
                        await self.bot.send_document(
                            chat_id=task.chat_id,
                            document=handle,
                            filename=item.name,
                            caption="ملف ناتج عن الطلب" if sent == 0 else None,
                        )
                    sent += 1
                    continue
                except Exception as exc:
                    self.log.warning("تعذر إرسال الملف مباشرة %s: %s", item.name, type(exc).__name__)
            # Large files: split into 49MB Telegram parts (works up to ~6GB).
            try:
                record = _DownloadLike(
                    path=str(item), size_bytes=size, filename=item.name,
                    direct_send_limit=49 * 1024 * 1024,
                )
                await deliver_download(self.bot, task.chat_id, record)  # type: ignore[arg-type]
                sent += 1
                continue
            except Exception as exc:
                self.log.warning("تعذر تقسيم الملف الكبير %s: %s", item.name, type(exc).__name__)
            # Last resort: mirror + send link (supports 4GB+ via chunked mirrors).
            try:
                mirrors = MirrorService(
                    item.parent,
                    extra_roots=(item.parent,),
                )
                provider, url = await mirrors.mirror_auto(item)
                pending_mirrors.append(f"{item.name} ({provider}): {url}")
                sent += 1
            except Exception as exc:
                self.log.warning("تعذر رفع الملف %s إلى مرآة: %s", item.name, type(exc).__name__)
        if pending_mirrors:
            try:
                await self.bot.send_message(
                    chat_id=task.chat_id,
                    text="☁️ ملفات كبيرة رُفعت إلى مرايا:\n" + "\n".join(pending_mirrors)[:3500],
                    disable_web_page_preview=False,
                )
            except Exception:
                pass
        return sent

    def final_text(self, text: str) -> str:
        """Source Markdown of the first page.

        The status message renders this page and the follow-ups start after it,
        so both halves must agree on where the first page ends.
        """
        from bridge.telegram.rendering.markdown_html import MAX_PAGE_CHARS, first_page

        body = text.strip() or empty_response_message()
        page = first_page(body, max_len=MAX_PAGE_CHARS)
        if page is not None and len(page.html) <= self.max_message_length:
            return page.plain or body
        if len(body) <= self.max_message_length:
            return body
        suffix = "\n\n… تتمة الرد في الرسائل التالية."
        return body[: max(1, self.max_message_length - len(suffix))].rstrip() + suffix

    async def send_followups(self, task: Any, full_text: str, first_chunk: str) -> None:
        """Send remaining pages + photos + open-page buttons after the status edit."""
        from bridge.telegram.rendering.markdown_html import build_pages
        from bridge.telegram.rich_delivery import (
            build_link_keyboard,
            extract_images,
            extract_links,
            send_photos,
            send_rendered_pages,
        )

        pages = build_pages(full_text)
        images = extract_images(full_text)
        links = extract_links(full_text)
        if len(pages) <= 1:
            # Status message already holds the full text: only add visuals.
            try:
                if images:
                    await send_photos(self.bot, task.chat_id, images)
            except Exception:
                pass
            if links:
                try:
                    keyboard = build_link_keyboard(links)
                    if keyboard is not None:
                        await self.bot.send_message(
                            chat_id=task.chat_id,
                            text="🔗 روابط المصادر — افتح صفحة أي موضوع:",
                            reply_markup=keyboard,
                            disable_web_page_preview=True,
                        )
                except Exception:
                    pass
            return
        # Page one already replaced the status message; send the rest.
        try:
            await send_rendered_pages(self.bot, task.chat_id, pages, start_index=1)
        except Exception as exc:
            self.log.warning("تعذر إرسال تتمة الرد: %s", type(exc).__name__)
        try:
            if images:
                await send_photos(self.bot, task.chat_id, images)
        except Exception:
            pass

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
