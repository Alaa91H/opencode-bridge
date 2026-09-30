"""Dynamic, task-local model routing without mutating global preference."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    capabilities: frozenset[str]
    quality: float = 0.5
    latency: float = 0.5
    reliability: float = 1.0
    cost: float = 0.5
    context_tokens: int = 0
    available: bool = True
    quota_remaining: float = 1.0
    recent_failures: int = 0


@dataclass(frozen=True)
class ModelTask:
    required: frozenset[str] = field(default_factory=frozenset)
    context_tokens: int = 0
    prefer_latency: float = 0.5
    prefer_cost: float = 0.5
    preferred_model: str | None = None


@dataclass(frozen=True)
class ModelRoute:
    primary: str
    fallback: tuple[str, ...]
    scores: tuple[tuple[str, float], ...]


class ModelRouter:
    def __init__(self, catalog: Iterable[ModelCandidate] = ()) -> None:
        self.replace_catalog(catalog)

    def replace_catalog(self, catalog: Iterable[ModelCandidate]) -> None:
        self._catalog = tuple(catalog)

    @staticmethod
    def _eligible(model: ModelCandidate, task: ModelTask) -> bool:
        return (model.available and model.quota_remaining > 0 and
                task.required.issubset(model.capabilities) and
                model.context_tokens >= task.context_tokens)

    @staticmethod
    def _score(model: ModelCandidate, task: ModelTask) -> float:
        capability = 1.0 if task.required.issubset(model.capabilities) else 0.0
        context_headroom = min(1.0, (model.context_tokens - task.context_tokens) / max(1, task.context_tokens)) if task.context_tokens else 1.0
        failure_penalty = min(1.0, model.recent_failures / 5)
        preferred_bonus = 0.15 if task.preferred_model == model.name else 0.0
        return (
            capability * 0.25
            + model.quality * 0.20
            + (1 - model.latency) * (0.15 + 0.10 * task.prefer_latency)
            + model.reliability * 0.15
            + (1 - model.cost) * (0.10 + 0.10 * task.prefer_cost)
            + context_headroom * 0.10
            + model.quota_remaining * 0.05
            + preferred_bonus
            - failure_penalty * 0.30
        )

    def route(self, task: ModelTask) -> ModelRoute:
        ranked = [(m.name, self._score(m, task)) for m in self._catalog if self._eligible(m, task)]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        if not ranked:
            raise LookupError("no model satisfies task capabilities/context/quota")
        return ModelRoute(ranked[0][0], tuple(name for name, _ in ranked[1:]), tuple(ranked))
