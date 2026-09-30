"""OpenTelemetry-compatible trace context primitives without mandatory SDK dependency."""

from __future__ import annotations

import hashlib
import random
import re
import secrets
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field

_TRACE: ContextVar[TraceContext | None] = ContextVar("bridge_trace", default=None)
_SECRET = re.compile(r"(?i)(token|secret|password|authorization|api[_-]?key)")


@dataclass(frozen=True)
class SamplingPolicy:
    ratio: float = 1.0

    def __post_init__(self) -> None:
        if not 0 <= self.ratio <= 1:
            raise ValueError("sampling ratio must be between 0 and 1")

    def sample(self) -> bool:
        return random.random() < self.ratio


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    sampled: bool = True
    baggage: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def root(cls, policy: SamplingPolicy | None = None) -> TraceContext:
        p = policy or SamplingPolicy()
        return cls(secrets.token_hex(16), secrets.token_hex(8), p.sample())

    def child(self) -> TraceContext:
        return TraceContext(self.trace_id, secrets.token_hex(8), self.sampled, self.baggage)

    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"


def activate(context: TraceContext):
    return _TRACE.set(context)


def reset(token) -> None:
    _TRACE.reset(token)


def current() -> TraceContext | None:
    return _TRACE.get()


def propagation_headers(context: TraceContext) -> dict[str, str]:
    return {"traceparent": context.traceparent()}


def redact_attributes(attributes: Mapping[str, object]) -> dict[str, object]:
    clean: dict[str, object] = {}
    for key, value in attributes.items():
        if _SECRET.search(key):
            clean[key] = "[REDACTED]"
        elif key in {"owner_id", "chat_id", "user_id"}:
            clean[key] = hashlib.sha256(str(value).encode()).hexdigest()[:16]
        elif key in {"prompt", "file_content", "attachment_content"}:
            clean[key] = "[CONTENT_REDACTED]"
        else:
            clean[key] = value
    return clean
