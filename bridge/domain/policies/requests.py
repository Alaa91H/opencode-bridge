"""Request policy adapter used by services without Telegram dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class RequestRejected(ValueError):
    """Raised when an instruction is rejected by the active execution policy."""


@dataclass(frozen=True)
class RequestGuard:
    """Compose existing policy checks behind a service-friendly boundary."""

    checks: tuple[Callable[[str], str | None], ...]

    def reject_reason(self, prompt: str) -> str | None:
        for check in self.checks:
            reason = check(prompt)
            if reason:
                return reason
        return None

    def ensure_allowed(self, prompt: str) -> None:
        reason = self.reject_reason(prompt)
        if reason:
            raise RequestRejected(reason)
