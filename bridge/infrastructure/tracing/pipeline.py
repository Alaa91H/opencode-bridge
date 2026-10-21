"""Trace stage contract from Telegram ingress to result delivery."""

from __future__ import annotations

from dataclasses import dataclass

from bridge.infrastructure.tracing.tracing import TraceContext, propagation_headers

TRACE_STAGES = ("telegram", "db", "opencode", "model", "media", "github", "result")


@dataclass(frozen=True)
class TracedStage:
    stage: str
    context: TraceContext

    def __post_init__(self) -> None:
        if self.stage not in TRACE_STAGES:
            raise ValueError(f"unknown trace stage: {self.stage}")

    @property
    def headers(self) -> dict[str, str]:
        return propagation_headers(self.context)


def child_stage(parent: TraceContext, stage: str) -> TracedStage:
    return TracedStage(stage, parent.child())
