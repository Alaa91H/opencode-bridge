"""Bounded context planning that preserves task state while compressing history."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


def estimate_tokens(text: str) -> int:
    # Conservative dependency-free estimator; providers may replace this adapter.
    return max(1, (len(text.encode("utf-8")) + 2) // 3) if text else 0


@dataclass(frozen=True)
class ContextItem:
    kind: str
    text: str
    relevance: float = 0.0
    required: bool = False

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class ContextBudget:
    max_tokens: int
    reserve_output_tokens: int = 1024

    @property
    def input_tokens(self) -> int:
        return max(0, self.max_tokens - self.reserve_output_tokens)


@dataclass(frozen=True)
class ContextCheckpoint:
    summary: str
    selected: tuple[ContextItem, ...]
    omitted_count: int
    estimated_tokens: int


class ContextManager:
    def __init__(self, budget: ContextBudget) -> None:
        self.budget = budget

    @staticmethod
    def summarize(items: Iterable[ContextItem], max_chars: int = 4000) -> str:
        pieces = [i.text.strip() for i in items if i.text.strip()]
        text = "\n".join(pieces)
        if len(text) <= max_chars:
            return text
        head = max_chars * 2 // 3
        tail = max_chars - head
        return text[:head] + "\n…[compressed history]…\n" + text[-tail:]

    @staticmethod
    def retrieve_history(items: Iterable[ContextItem], query: str, limit: int = 12) -> list[ContextItem]:
        terms = {x.casefold() for x in query.split() if len(x) > 2}
        scored = []
        for index, item in enumerate(items):
            lexical = sum(item.text.casefold().count(t) for t in terms)
            scored.append((item.required, lexical + item.relevance, -index, item))
        scored.sort(key=lambda row: (not row[0], -row[1], -row[2]))
        return [row[3] for row in scored[:limit]]

    def checkpoint(self, *, prompt: str, history: Iterable[ContextItem] = (),
                   attachments: Iterable[ContextItem] = ()) -> ContextCheckpoint:
        capacity = self.budget.input_tokens
        prompt_item = ContextItem("prompt", prompt, required=True)
        candidates = [prompt_item, *history, *attachments]
        required = [i for i in candidates if i.required]
        optional = sorted((i for i in candidates if not i.required), key=lambda i: (-i.relevance, i.tokens))
        selected: list[ContextItem] = []
        used = 0
        for item in [*required, *optional]:
            if used + item.tokens <= capacity:
                selected.append(item)
                used += item.tokens
        omitted = len(candidates) - len(selected)
        if omitted:
            omitted_items = [i for i in candidates if i not in selected]
            summary = self.summarize(omitted_items, max_chars=max(256, capacity * 2))
            summary_item = ContextItem("summary", summary, relevance=1.0)
            if used + summary_item.tokens <= capacity:
                selected.append(summary_item)
                used += summary_item.tokens
        return ContextCheckpoint(self.summarize(selected), tuple(selected), omitted, used)
