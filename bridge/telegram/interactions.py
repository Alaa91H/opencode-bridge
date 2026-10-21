"""Keep track of what the agent is waiting for, per chat, until it is answered.

A Telegram button carries only a request identifier, so the state of a
conversation that needs more than one tap — a multi-select question, an answer
typed as text — has to live somewhere between the card and the click. This is
that somewhere: a small in-process registry with a TTL, because a pending
request the server has forgotten must expire rather than answer a later,
unrelated task.

Nothing sensitive is kept here: labels and identifiers only, and the registry is
dropped as soon as the request is answered or the card is stale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from bridge.telegram.rendering.interactions import Question

DEFAULT_TTL_SECONDS = 3600.0


@dataclass
class PendingQuestion:
    """One agent question a card is waiting on."""

    request_id: str
    chat_id: int
    message_id: int
    questions: tuple[Question, ...]
    created_at: float = field(default_factory=time.monotonic)
    chosen: dict[int, set[str]] = field(default_factory=dict)
    text_answers: dict[int, str] = field(default_factory=dict)
    awaiting_text: bool = False

    def is_expired(self, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
        return time.monotonic() - self.created_at > ttl

    def toggle(self, question_index: int, label: str) -> None:
        bucket = self.chosen.setdefault(question_index, set())
        if label in bucket:
            bucket.discard(label)
        else:
            bucket.add(label)

    def answers(self) -> list[list[str]] | None:
        """The answer list in question order, or None while one is missing."""
        ordered: list[list[str]] = []
        for index, item in enumerate(self.questions):
            text = self.text_answers.get(index)
            if text:
                ordered.append([text])
                continue
            labels = sorted(self.chosen.get(index, set()))
            if not labels:
                return None
            if not item.multiple and len(labels) > 1:
                labels = labels[:1]
            ordered.append(labels)
        return ordered or None


class InteractionRegistry:
    """Pending permissions and questions, keyed by the chat that can answer them."""

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._questions: dict[tuple[int, str], PendingQuestion] = {}
        self._cards: dict[tuple[int, int], str] = {}
        self._permissions: dict[str, int] = {}

    def remember_question(
        self, chat_id: int, message_id: int, request_id: str, questions: tuple[Question, ...],
    ) -> PendingQuestion:
        self._expire()
        pending = PendingQuestion(
            request_id=request_id,
            chat_id=chat_id,
            message_id=message_id,
            questions=questions,
        )
        self._questions[(chat_id, request_id)] = pending
        self._cards[(chat_id, message_id)] = request_id
        return pending

    def remember_permission(self, chat_id: int, request_id: str) -> None:
        self._expire()
        self._permissions[request_id] = chat_id

    def question(self, chat_id: int, request_id: str) -> PendingQuestion | None:
        pending = self._questions.get((chat_id, request_id))
        if pending is None:
            return None
        if pending.is_expired(self._ttl):
            self.forget_question(chat_id, request_id)
            return None
        return pending

    def question_for_card(self, chat_id: int, message_id: int) -> PendingQuestion | None:
        request_id = self._cards.get((chat_id, message_id))
        if request_id is None:
            return None
        return self.question(chat_id, request_id)

    def forget_question(self, chat_id: int, request_id: str) -> None:
        pending = self._questions.pop((chat_id, request_id), None)
        if pending is not None:
            self._cards.pop((pending.chat_id, pending.message_id), None)

    def forget_permission(self, request_id: str) -> None:
        self._permissions.pop(request_id, None)

    def permission_chat(self, request_id: str) -> int | None:
        return self._permissions.get(request_id)

    def open_questions(self, chat_id: int) -> list[PendingQuestion]:
        self._expire()
        return [
            pending
            for (owner, _request), pending in self._questions.items()
            if owner == chat_id
        ]

    def _expire(self) -> None:
        stale = [
            key for key, pending in self._questions.items() if pending.is_expired(self._ttl)
        ]
        for key in stale:
            self.forget_question(*key)
