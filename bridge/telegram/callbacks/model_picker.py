"""Telegram callback adapter for the model/reasoning-level picker."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.model_selection_service import ModelSelectionError
from bridge.telegram.rendering import model_picker as picker

CallbackAnswer = Callable[..., Any]


class ModelPickerCallbackAdapter:
    """Turn one short button payload into a validated catalog selection.

    Authorization is re-checked here because ``CallbackQueryHandler`` bypasses
    the ``@authorized`` decorator that guards command handlers.
    """

    def __init__(
        self,
        *,
        service: Any,
        is_allowed: Callable[[Update], bool],
        answer: CallbackAnswer,
        logger: logging.Logger | None = None,
    ) -> None:
        self.service = service
        self.is_allowed = is_allowed
        self.answer = answer
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def _owner(update: Update) -> str | None:
        user = update.effective_user
        return str(user.id) if user is not None else None

    async def show(self, update: Update) -> None:
        """Render the model list for ``/model``."""
        owner_id = self._owner(update)
        if owner_id is None:
            return
        if not self.is_allowed(update):
            message = update.effective_message
            if message is not None:
                await message.reply_text("غير مصرح لك باستخدام هذا الأمر.")
            return
        await self._render_into_message(update, owner_id)

    async def _render_into_message(self, update: Update, owner_id: str) -> None:
        view = await self.service.view(owner_id)
        text, keyboard = picker.model_page(view)
        query = update.callback_query
        if query is not None and query.message is not None:
            await update.get_bot().edit_message_text(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or not isinstance(query.data, str):
            return
        if not query.data.startswith(f"{picker.CALLBACK_PREFIX}:"):
            return
        if not self.is_allowed(update):
            await query.answer("غير مصرح لك باستخدام هذا الزر.", show_alert=True)
            return
        owner_id = self._owner(update)
        if owner_id is None:
            return
        parts = query.data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "noop":
            await query.answer()
            return
        if action == "open":
            await self._render_into_message(update, owner_id)
            await query.answer()
            return
        try:
            await self._dispatch(update, query, owner_id, action, parts)
        except ModelSelectionError as exc:
            self.log.info("تعذر تنفيذ اختيار النموذج: %s", exc)
            await query.answer(str(exc), show_alert=True)
            await self._refresh(update, owner_id)
        except Exception as exc:
            self.log.exception("فشل التعامل مع زر اختيار النموذج")
            await query.answer("حدثت مشكلة غير متوقعة أثناء اختيار النموذج.", show_alert=True)

    async def _dispatch(
        self,
        update: Update,
        query: Any,
        owner_id: str,
        action: str,
        parts: list[str],
    ) -> None:
        if action == "m":
            view = await self.service.select_model(owner_id, _index(parts, 2))
            text, keyboard = picker.variant_levels(view, _index(parts, 2))
            await self._edit(update, text, keyboard)
            await query.answer()
            return
        if action == "v":
            model_index = _index(parts, 2)
            variant_index = _index(parts, 3)
            view = await self.service.select_variant(owner_id, model_index, variant_index)
            choice = view.model_at(model_index)
            label = choice.label if choice is not None else "—"
            variant_text = (
                picker.variant_label(view.variant_choice(model_index, variant_index).variant_id or "")
                if view.variant_choice(model_index, variant_index) is not None
                else "تلقائي"
            )
            text, keyboard = picker.model_page(view)
            await self._edit(update, picker.confirmed_text(view, model_label=label, variant_label_text=variant_text), keyboard)
            await query.answer("تم اعتماد الاختيار ✅", show_alert=False)
            return
        if action == "auto":
            view = await self.service.auto(owner_id)
            text, keyboard = picker.model_page(view)
            await self._edit(update, text, keyboard)
            await query.answer("رجعت للاختيار التلقائي ✅")
            return
        if action == "back":
            view = await self.service.view(owner_id)
            text, keyboard = picker.model_page(view)
            await self._edit(update, text, keyboard)
            await query.answer()
            return
        if action == "p":
            view = await self.service.view(owner_id)
            text, keyboard = picker.model_page(view, page=_index(parts, 2))
            await self._edit(update, text, keyboard)
            await query.answer()
            return
        await query.answer()

    async def _edit(self, update: Update, text: str, keyboard: Any) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        await update.get_bot().edit_message_text(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=text,
            reply_markup=keyboard,
        )

    async def _refresh(self, update: Update, owner_id: str) -> None:
        try:
            view = await self.service.view(owner_id)
            text, keyboard = picker.model_page(view)
            await self._edit(update, text, keyboard)
        except Exception as exc:
            self.log.info("تعذر تحديث قائمة النماذج: %s", type(exc).__name__)


def _index(parts: list[str], position: int) -> int:
    try:
        return int(parts[position])
    except (IndexError, ValueError):
        return -1
