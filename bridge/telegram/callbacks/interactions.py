"""Answer the agent from Telegram: approve, deny, choose, or write an answer.

This adapter is the whole second direction of the conversation. A button press
becomes a decision on the OpenCode server, the card is replaced with what was
answered, and the agent carries on. Every path re-checks that the actor is
allowed, because ``CallbackQueryHandler`` bypasses the ``@authorized`` decorator
that guards command handlers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bridge.telegram.interactions import InteractionRegistry
from bridge.telegram.rendering import interactions as cards

CallbackAnswer = Callable[..., Any]

STALE_MESSAGE = "انتهت صلاحية هذا الطلب؛ الوكيل لم يعد ينتظر إجابته."
FAILED_MESSAGE = "تعذر تسجيل الرد على الوكيل. جرّب مرة أخرى."


class AgentInteractionCallbacks:
    """Turn one button press (or one replied-to message) into an agent answer."""

    def __init__(
        self,
        *,
        client: Any,
        registry: InteractionRegistry,
        is_allowed: Callable[[Update], bool],
        audit_write: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.is_allowed = is_allowed
        self.audit_write = audit_write
        self.log = logger or logging.getLogger(__name__)
        self.bot: Any = None

    # ------------------------------------------------------------------ cards

    async def send_permission(self, card: cards.PermissionCard, chat_id: int) -> int | None:
        """Post a permission request to the chat that can answer it."""
        self.registry.remember_permission(chat_id, card.request_id)
        return await self._post(chat_id, cards.permission_card(card),
                                _keyboard(cards.permission_keyboard(card)))

    async def send_question(
        self, request_id: str, questions: tuple[cards.Question, ...], chat_id: int,
    ) -> int | None:
        """Post the agent's question with its options as buttons."""
        message_id = await self._post(
            chat_id,
            cards.question_card(questions),
            _keyboard(cards.question_keyboard(request_id, questions)),
        )
        if message_id is not None:
            self.registry.remember_question(chat_id, message_id, request_id, questions)
        return message_id

    async def _post(self, chat_id: int, text: str, keyboard: Any) -> int | None:
        bot = self.bot
        if bot is None:
            return None
        try:
            message = await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # a card must never fail a task
            self.log.info("تعذر عرض طلب تفاعل: %s", type(exc).__name__)
            return None
        return int(getattr(message, "message_id", 0) or 0) or None

    def attach_bot(self, bot: Any) -> None:
        """The bot object, set once the application is built."""
        self.bot = bot

    # --------------------------------------------------------------- handlers

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or not isinstance(query.data, str):
            return
        try:
            parts = cards.parse_payload(query.data)
        except ValueError:
            return
        if not self.is_allowed(update):
            await query.answer("غير مصرح لك بالرد على هذا الطلب.", show_alert=True)
            return
        chat_id = _chat_id(update)
        if chat_id is None:
            return
        try:
            if parts[0] == "permission":
                await self._answer_permission(query, chat_id, parts[1], parts[2])
            elif parts[0] == "option":
                await self._toggle_option(query, chat_id, parts[1], int(parts[2]), int(parts[3]))
            else:
                await self._question_action(query, chat_id, parts[1], parts[2])
        except Exception as exc:  # a button must never crash the bot
            self.log.info("تعذر تنفيذ رد التفاعل: %s", type(exc).__name__)
            await query.answer(FAILED_MESSAGE, show_alert=True)

    async def handle_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """A reply to a question card is the answer to that question."""
        message = update.effective_message
        if message is None or not self.is_allowed(update):
            return
        chat_id = _chat_id(update)
        replied = getattr(message, "reply_to_message", None)
        if chat_id is None or replied is None:
            return
        pending = self.registry.question_for_card(chat_id, int(replied.message_id))
        if pending is None:
            return
        answer = (message.text or "").strip()
        if not answer:
            return
        try:
            await self._submit_text(pending, answer)
        except Exception as exc:  # a failed answer must still leave the card honest
            self.log.info("تعذر إرسال الإجابة النصية: %s", type(exc).__name__)
            await message.reply_text(FAILED_MESSAGE)
            return
        self._audit("interaction_text_answer", request_id=pending.request_id)
        await message.reply_text("✅ أُرسلت إجابتك النصية إلى الوكيل.")

    # ------------------------------------------------------------- internals

    async def _answer_permission(
        self, query: Any, chat_id: int, request_id: str, decision: str,
    ) -> None:
        await query.answer("… عم أرسل القرار")
        ok = await self.client.reply_permission(request_id, decision)
        if not ok:
            await self._replace(query, STALE_MESSAGE)
            return
        self.registry.forget_permission(request_id)
        self._audit("interaction_permission", request_id=request_id, decision=decision)
        await self._replace(query, cards.answered_note("permission", decision))

    async def _toggle_option(
        self, query: Any, chat_id: int, request_id: str, question_index: int, option_index: int,
    ) -> None:
        pending = self.registry.question(chat_id, request_id)
        if pending is None:
            await query.answer(STALE_MESSAGE, show_alert=True)
            await self._replace(query, STALE_MESSAGE)
            return
        question = _at(pending.questions, question_index)
        option = _at(question.options, option_index) if question is not None else None
        if option is None:
            await query.answer("هذا الخيار لم يعد متاحًا.", show_alert=True)
            return
        label = option[0]
        if question is not None and not question.multiple:
            # One tap answers the whole question: the agent is waiting, and a
            # second tap would be a second answer to a question already sent.
            pending.text_answers.pop(question_index, None)
            pending.chosen = {
                index: values for index, values in pending.chosen.items() if index != question_index
            }
            pending.chosen[question_index] = {label}
        else:
            pending.toggle(question_index, label)
        await query.answer(f"✔ {label}")
        await self._refresh_card(query, pending)
        await self._maybe_submit(query, chat_id, pending)

    async def _question_action(self, query: Any, chat_id: int, request_id: str, action: str) -> None:
        pending = self.registry.question(chat_id, request_id)
        if pending is None:
            await query.answer(STALE_MESSAGE, show_alert=True)
            await self._replace(query, STALE_MESSAGE)
            return
        if action == "r":
            await query.answer("… عم ألغيت السؤال")
            ok = await self.client.reject_question(request_id)
            self.registry.forget_question(chat_id, request_id)
            if not ok:
                await self._replace(query, STALE_MESSAGE)
                return
            self._audit("interaction_question_rejected", request_id=request_id)
            await self._replace(query, cards.answered_note("question", "r"))
            return
        if action == "t":
            pending.awaiting_text = not pending.awaiting_text
            if pending.awaiting_text:
                await query.answer("اكتب إجابتك كرد على هذه الرسالة", show_alert=True)
            else:
                await query.answer("أُلغي وضع الإجابة النصية.")
            await self._refresh_card(query, pending)
            return
        answers = pending.answers()
        if answers is None:
            await query.answer("اختر إجابة أولًا.", show_alert=True)
            return
        ok = await self.client.reply_question(request_id, answers)
        if not ok:
            await self._replace(query, STALE_MESSAGE)
            return
        self.registry.forget_question(chat_id, request_id)
        self._audit("interaction_question_answer", request_id=request_id)
        await self._replace(query, cards.answered_note("question", "c"))

    async def _submit_text(self, pending: Any, answer: str) -> None:
        """Send a typed answer without discarding the taps already made."""
        index = _first_open_index(pending)
        target = 0 if index is None else index
        ok = await self.client.reply_question(pending.request_id, _answers_with(pending, target, answer))
        if not ok:
            raise RuntimeError("the agent no longer waits for this question")
        self.registry.forget_question(pending.chat_id, pending.request_id)

    async def _maybe_submit(self, query: Any, chat_id: int, pending: Any) -> None:
        """A multi-select question submits itself once nothing is left open."""
        if pending.awaiting_text or any(item.multiple for item in pending.questions):
            return
        answers = pending.answers()
        if answers is None:
            return
        ok = await self.client.reply_question(pending.request_id, answers)
        if not ok:
            await self._replace(query, STALE_MESSAGE)
            return
        self.registry.forget_question(chat_id, pending.request_id)
        self._audit("interaction_question_answer", request_id=pending.request_id)
        await self._replace(query, cards.answered_note("question", "c"))

    async def _refresh_card(self, query: Any, pending: Any) -> None:
        keyboard = cards.question_keyboard(pending.request_id, pending.questions, pending.chosen)
        await self._edit(query, cards.question_card(pending.questions), _keyboard(keyboard))

    async def _replace(self, query: Any, text: str) -> None:
        message = getattr(query, "message", None)
        if message is None:
            return
        try:
            await query.edit_message_text(text=text, reply_markup=None)
        except Exception as exc:  # a failed answer must still leave the card honest
            self.log.info("تعذر تحديث بطاقة التفاعل: %s", type(exc).__name__)

    async def _edit(self, query: Any, text: str, keyboard: Any) -> None:
        try:
            await query.edit_message_text(text=text, reply_markup=keyboard)
        except Exception as exc:  # a failed answer must still leave the card honest
            self.log.info("تعذر تحديث بطاقة السؤال: %s", type(exc).__name__)

    def _audit(self, event: str, **details: Any) -> None:
        if self.audit_write is None:
            return
        try:
            self.audit_write(event, "accepted", details=details)
        except Exception as exc:  # a failed answer must still leave the card honest
            self.log.debug("تعذر تسجيل حدث التفاعل: %s", type(exc).__name__)


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=payload) for label, payload in row]
         for row in rows if row]
    )


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    return int(chat.id) if chat is not None else None


def _at(items: tuple[Any, ...], index: int) -> Any | None:
    if index < 0 or index >= len(items):
        return None
    return items[index]


def _first_open_index(pending: Any) -> int | None:
    for index in range(len(pending.questions)):
        if not pending.chosen.get(index) and not pending.text_answers.get(index):
            return index
    return None


def _answers_with(pending: Any, index: int, answer: str) -> list[list[str]]:
    answers = pending.answers() or [[] for _ in pending.questions]
    while len(answers) <= index:
        answers.append([])
    answers[index] = [answer]
    return answers
