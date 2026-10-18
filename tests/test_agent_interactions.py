"""The agent must be able to ask, and Telegram must be able to answer.

The bridge was one-way: it reported that the agent was waiting for a decision or
a question and then nothing could answer either. The task then burned its whole
budget until the server timed out. These tests pin both directions — the cards
that reach the chat, and the decisions that go back to the server — including
the refusals: a stranger's button press, a forged payload, a question the server
has already forgotten.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bridge.telegram.callbacks.interactions import (
    STALE_MESSAGE,
    AgentInteractionCallbacks,
)
from bridge.telegram.interactions import InteractionRegistry
from bridge.telegram.rendering import interactions as cards

PERMISSION_EVENT = {
    "type": "permission.asked",
    "properties": {
        "id": "per_abc123",
        "sessionID": "ses_1",
        "permission": "bash",
        "patterns": ["git push --force"],
        "metadata": {"secret": "never-render-me"},
        "always": ["git status"],
    },
}

PERMISSION_V2_EVENT = {
    "type": "permission.v2.asked",
    "properties": {
        "id": "per_v2",
        "sessionID": "ses_1",
        "action": "edit",
        "resources": ["/home/ubuntu/app/main.py"],
        "save": [],
    },
}

QUESTION_EVENT = {
    "type": "question.asked",
    "properties": {
        "id": "que_1",
        "sessionID": "ses_1",
        "questions": [
            {
                "header": "النشر",
                "question": "هل أنشر الآن؟",
                "options": [
                    {"label": "نعم", "description": "انشر مباشرة"},
                    {"label": "لاحقًا", "description": "أجّل النشر"},
                ],
                "multiple": False,
                "custom": True,
            }
        ],
        "tool": {"messageID": "msg_1", "callID": "call_1"},
    },
}


class EventParsingTests(unittest.TestCase):
    def test_a_permission_event_becomes_a_card(self) -> None:
        card = cards.parse_permission_event(PERMISSION_EVENT)
        self.assertIsNotNone(card)
        self.assertEqual(card.request_id, "per_abc123")
        self.assertEqual(card.action, "bash")
        self.assertEqual(card.targets, ("git push --force",))

    def test_the_second_permission_generation_is_read(self) -> None:
        card = cards.parse_permission_event(PERMISSION_V2_EVENT)
        self.assertIsNotNone(card)
        self.assertEqual(card.action, "edit")
        self.assertEqual(card.targets, ("/home/ubuntu/app/main.py",))

    def test_metadata_is_never_read_into_the_card(self) -> None:
        card = cards.parse_permission_event(PERMISSION_EVENT)
        self.assertNotIn("never-render-me", str(card))

    def test_an_unrelated_event_is_not_a_request(self) -> None:
        self.assertIsNone(cards.parse_permission_event({"type": "session.idle", "properties": {}}))
        self.assertIsNone(cards.parse_permission_event({"type": "permission.asked", "properties": {}}))

    def test_a_question_event_becomes_questions(self) -> None:
        parsed = cards.parse_question_event(QUESTION_EVENT)
        self.assertIsNotNone(parsed)
        request_id, questions = parsed
        self.assertEqual(request_id, "que_1")
        self.assertEqual(questions[0].header, "النشر")
        self.assertEqual(len(questions[0].options), 2)
        self.assertTrue(questions[0].custom)


class PayloadTests(unittest.TestCase):
    def test_every_payload_fits_telegrams_limit(self) -> None:
        payloads = [
            cards.permission_payload("per_0123456789abcdef0123456789abcdef", cards.PERMISSION_ONCE),
            cards.permission_payload("per_0123456789abcdef0123456789abcdef", cards.PERMISSION_ALWAYS),
            cards.permission_payload("per_0123456789abcdef0123456789abcdef", cards.PERMISSION_REJECT),
            cards.option_payload("que_0123456789abcdef0123456789abcdef", 2, 9),
            cards.question_payload("que_0123456789abcdef0123456789abcdef", cards.QUESTION_REJECT),
        ]
        for payload in payloads:
            self.assertLessEqual(len(payload.encode()), cards.MAX_CALLBACK_BYTES)
            self.assertEqual(cards.parse_payload(payload)[1], payload.split(":")[2])

    def test_a_forged_payload_is_rejected(self) -> None:
        for payload in (
            "ag:p:not-an-id:once",
            "ag:p:per_1:delete-everything",
            "ag:q:per_1:r",
            "ag:o:per_1:0:1",
            "ag:o:que_1:x:1",
            "ag:q:que_1:z",
            "other:payload",
            "ag:",
        ):
            with self.assertRaises(ValueError):
                cards.parse_payload(payload)

    def test_card_text_escapes_the_agents_own_markup(self) -> None:
        question = cards.Question(
            header="<b>", question="هل <i>ننشر</i> & نبدأ؟", options=(("نعم <x>", ""),),
        )
        html = cards.question_card((question,))
        self.assertNotIn("<i>ننشر</i>", html)
        self.assertIn("&lt;i&gt;", html)


class RegistryTests(unittest.TestCase):
    def test_a_card_is_found_by_the_message_it_replied_to(self) -> None:
        registry = InteractionRegistry()
        question = cards.Question(header="", question="نعم؟", options=(("نعم", ""),))
        registry.remember_question(7, 42, "que_1", (question,))
        self.assertIsNotNone(registry.question_for_card(7, 42))
        self.assertIsNone(registry.question_for_card(7, 43))

    def test_another_chat_cannot_answer_this_card(self) -> None:
        registry = InteractionRegistry()
        question = cards.Question(header="", question="نعم؟", options=(("نعم", ""),))
        registry.remember_question(7, 42, "que_1", (question,))
        self.assertIsNone(registry.question(99, "que_1"))

    def test_a_multi_select_needs_every_question_answered(self) -> None:
        registry = InteractionRegistry()
        questions = (
            cards.Question(header="أ", question="الأولى", options=(("١", ""),)),
            cards.Question(header="ب", question="الثانية", options=(("٢", ""),)),
        )
        pending = registry.remember_question(7, 42, "que_1", questions)
        pending.toggle(0, "١")
        self.assertIsNone(pending.answers())
        pending.toggle(1, "٢")
        self.assertEqual(pending.answers(), [["١"], ["٢"]])

    def test_a_single_select_never_returns_two_labels(self) -> None:
        pending = InteractionRegistry().remember_question(
            7, 42, "que_1",
            (cards.Question(header="", question="نعم؟", options=(("نعم", ""), ("لاحقًا", ""))),),
        )
        pending.toggle(0, "نعم")
        pending.toggle(0, "لاحقًا")
        answers = pending.answers()
        self.assertIsNotNone(answers)
        self.assertEqual(len(answers), 1)
        self.assertEqual(len(answers[0]), 1)
        self.assertIn(answers[0][0], {"نعم", "لاحقًا"})

    def test_an_expired_card_is_gone(self) -> None:
        registry = InteractionRegistry(ttl=0.0)
        registry.remember_question(
            7, 42, "que_1", (cards.Question(header="", question="نعم؟"),),
        )
        self.assertIsNone(registry.question(7, "que_1"))


class _Query:
    def __init__(self, data: str, chat_id: int = 7, message_id: int = 42) -> None:
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id, message_id=message_id)
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.edit_errors = 0

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append(str(text))

    async def edit_message_text(self, text: str, reply_markup: object = None) -> None:
        if self.edit_errors:
            raise RuntimeError("message is not modified")
        self.edits.append(text)


def _update(query: _Query, user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=query.message.chat_id),
        effective_message=query.message,
        message=query.message,
    )


def _adapter(allowed: bool = True, **client_methods: object) -> AgentInteractionCallbacks:
    methods: dict[str, object] = {
        "reply_permission": AsyncMock(return_value=True),
        "reply_question": AsyncMock(return_value=True),
        "reject_question": AsyncMock(return_value=True),
    }
    methods.update(client_methods)
    client = SimpleNamespace(**methods)
    adapter = AgentInteractionCallbacks(
        client=client,
        registry=InteractionRegistry(),
        is_allowed=lambda _update: allowed,
    )
    adapter.client = client
    return adapter


class PermissionAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_approving_sends_the_decision_to_the_server(self) -> None:
        adapter = _adapter()
        card = cards.parse_permission_event(PERMISSION_EVENT)
        query = _Query(cards.permission_payload(card.request_id, cards.PERMISSION_ONCE))
        await adapter.handle(_update(query), SimpleNamespace())
        adapter.client.reply_permission.assert_awaited_once_with("per_abc123", "once")
        self.assertTrue(any("تمت الموافقة" in text for text in query.edits))

    async def test_always_and_reject_are_distinct_decisions(self) -> None:
        for code, expected in (
            (cards.PERMISSION_ALWAYS, "always"),
            (cards.PERMISSION_REJECT, "reject"),
        ):
            adapter = _adapter()
            query = _Query(cards.permission_payload("per_abc123", code))
            await adapter.handle(_update(query), SimpleNamespace())
            adapter.client.reply_permission.assert_awaited_once_with("per_abc123", expected)

    async def test_a_strangers_press_never_reaches_the_server(self) -> None:
        adapter = _adapter(allowed=False)
        query = _Query(cards.permission_payload("per_abc123", cards.PERMISSION_ONCE))
        await adapter.handle(_update(query), SimpleNamespace())
        adapter.client.reply_permission.assert_not_awaited()
        self.assertTrue(any("غير مصرح" in answer for answer in query.answers))

    async def test_a_decision_the_server_forgets_says_so_plainly(self) -> None:
        adapter = _adapter(reply_permission=AsyncMock(return_value=False))
        query = _Query(cards.permission_payload("per_abc123", cards.PERMISSION_ONCE))
        await adapter.handle(_update(query), SimpleNamespace())
        self.assertIn(STALE_MESSAGE, query.edits)

    async def test_a_button_that_raises_never_crashes_the_bot(self) -> None:
        adapter = _adapter(reply_permission=AsyncMock(side_effect=RuntimeError("boom")))
        query = _Query(cards.permission_payload("per_abc123", cards.PERMISSION_ONCE))
        await adapter.handle(_update(query), SimpleNamespace())
        self.assertTrue(any("تعذر" in answer for answer in query.answers))

    async def test_the_permission_card_reaches_the_chat(self) -> None:
        adapter = _adapter()
        adapter.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=9)))
        card = cards.parse_permission_event(PERMISSION_EVENT)
        message_id = await adapter.send_permission(card, 7)
        self.assertEqual(message_id, 9)
        sent = adapter.bot.send_message.await_args.kwargs
        self.assertIn("يطلب إذنًا", sent["text"])
        self.assertNotIn("never-render-me", sent["text"])
        self.assertIsNotNone(sent["reply_markup"])


class QuestionAnswerTests(unittest.IsolatedAsyncioTestCase):
    def _pending(self, adapter: AgentInteractionCallbacks, **kwargs: object) -> object:
        question = cards.Question(header="النشر", question="هل أنشر؟", options=(("نعم", ""),))
        return adapter.registry.remember_question(7, 42, "que_1", (question,), **kwargs)

    async def test_one_tap_answers_a_single_choice_question(self) -> None:
        adapter = _adapter()
        self._pending(adapter)
        query = _Query(cards.option_payload("que_1", 0, 0))
        await adapter.handle(_update(query), SimpleNamespace())
        adapter.client.reply_question.assert_awaited_once_with("que_1", [["نعم"]])
        self.assertTrue(any("تم تأكيد" in text for text in query.edits))

    async def test_a_multi_select_waits_for_the_confirm_button(self) -> None:
        adapter = _adapter()
        question = cards.Question(
            header="الأقسام", question="اختر", options=(("أ", ""), ("ب", "")), multiple=True,
        )
        adapter.registry.remember_question(7, 42, "que_1", (question,))
        await adapter.handle(_update(_Query(cards.option_payload("que_1", 0, 0))), SimpleNamespace())
        await adapter.handle(_update(_Query(cards.option_payload("que_1", 0, 1))), SimpleNamespace())
        adapter.client.reply_question.assert_not_awaited()
        await adapter.handle(
            _update(_Query(cards.question_payload("que_1", cards.QUESTION_CONFIRM))),
            SimpleNamespace(),
        )
        adapter.client.reply_question.assert_awaited_once_with("que_1", [["أ", "ب"]])

    async def test_tapping_the_same_option_twice_deselects_it(self) -> None:
        adapter = _adapter()
        question = cards.Question(
            header="", question="اختر", options=(("أ", ""),), multiple=True,
        )
        pending = adapter.registry.remember_question(7, 42, "que_1", (question,))
        payload = cards.option_payload("que_1", 0, 0)
        await adapter.handle(_update(_Query(payload)), SimpleNamespace())
        self.assertEqual(pending.chosen[0], {"أ"})
        await adapter.handle(_update(_Query(payload)), SimpleNamespace())
        self.assertEqual(pending.chosen[0], set())

    async def test_cancelling_the_question_releases_the_agent(self) -> None:
        adapter = _adapter()
        self._pending(adapter)
        query = _Query(cards.question_payload("que_1", cards.QUESTION_REJECT))
        await adapter.handle(_update(query), SimpleNamespace())
        adapter.client.reject_question.assert_awaited_once_with("que_1")
        self.assertTrue(any("أُلغي السؤال" in text for text in query.edits))

    async def test_a_reply_to_the_card_becomes_the_answer(self) -> None:
        adapter = _adapter()
        self._pending(adapter)
        message = SimpleNamespace(
            chat_id=7, message_id=50, text="نعم انشر الآن", reply_to_message=SimpleNamespace(message_id=42),
        )
        update = SimpleNamespace(
            effective_message=message, message=message,
            effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=7),
            callback_query=None,
        )
        sent = SimpleNamespace(reply_text=AsyncMock())
        message.reply_text = sent.reply_text
        await adapter.handle_reply(update, SimpleNamespace())
        adapter.client.reply_question.assert_awaited_once_with("que_1", [["نعم انشر الآن"]])

    async def test_a_reply_to_an_ordinary_message_is_not_an_answer(self) -> None:
        adapter = _adapter()
        message = SimpleNamespace(
            chat_id=7, message_id=50, text="مرحبا", reply_to_message=SimpleNamespace(message_id=11),
        )
        message.reply_text = AsyncMock()
        update = SimpleNamespace(
            effective_message=message, message=message,
            effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(id=7),
            callback_query=None,
        )
        await adapter.handle_reply(update, SimpleNamespace())
        adapter.client.reply_question.assert_not_awaited()

    async def test_a_stale_question_card_says_the_agent_moved_on(self) -> None:
        adapter = _adapter()
        query = _Query(cards.question_payload("que_missing", cards.QUESTION_REJECT))
        await adapter.handle(_update(query), SimpleNamespace())
        adapter.client.reject_question.assert_not_awaited()
        self.assertIn(STALE_MESSAGE, query.edits)

    async def test_the_question_card_reaches_the_chat(self) -> None:
        adapter = _adapter()
        adapter.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=9)))
        parsed = cards.parse_question_event(QUESTION_EVENT)
        message_id = await adapter.send_question(parsed[0], parsed[1], 7)
        self.assertEqual(message_id, 9)
        sent = adapter.bot.send_message.await_args.kwargs
        self.assertIn("يسألك", sent["text"])
        self.assertIn("هل أنشر الآن؟", sent["text"])
        self.assertEqual(adapter.registry.question_for_card(7, 9).request_id, "que_1")

    async def test_a_failed_card_never_raises_into_the_task(self) -> None:
        adapter = _adapter()
        adapter.bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("flood")))
        card = cards.parse_permission_event(PERMISSION_EVENT)
        self.assertIsNone(await adapter.send_permission(card, 7))


if __name__ == "__main__":
    unittest.main()
