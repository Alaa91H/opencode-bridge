"""Quota-aware OpenCode credential selection.

The pool distinguishes credential-scoped limits from Zen free-model IP limits.
It never rotates credentials for zen_free_quota because that bucket is shared by the host IP upstream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Iterable


@dataclass(frozen=True)
class OpenCodeCredential:
    name: str
    secret: str
    weight: int = 1

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.secret.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class CredentialSnapshot:
    name: str
    fingerprint: str
    state: str
    cooldown_until: datetime | None = None
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_used_at: datetime | None = None


@dataclass
class _RuntimeState:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
    last_used_at: datetime | None = None
    disabled: bool = False


class CredentialPool:
    """Round-robin pool for legitimate credential-scoped failover."""

    def __init__(self, credentials: Iterable[OpenCodeCredential] = ()) -> None:
        self._credentials = tuple(credentials)
        names = [item.name for item in self._credentials]
        if len(names) != len(set(names)):
            raise ValueError("credential names must be unique")
        if any(not item.name or not item.secret for item in self._credentials):
            raise ValueError("credential name and secret must be non-empty")
        if any(item.weight < 1 for item in self._credentials):
            raise ValueError("credential weight must be >= 1")
        self._state = {item.name: _RuntimeState() for item in self._credentials}
        self._lock = Lock()

    def __len__(self) -> int:
        return len(self._credentials)

    def select(self, *, now: datetime | None = None, exclude: set[str] | None = None) -> OpenCodeCredential | None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        blocked = exclude or set()
        with self._lock:
            if not self._credentials:
                return None
            for offset in range(len(self._credentials)):
                index = (self._cursor + offset) % len(self._credentials)
                item = self._credentials[index]
                runtime = self._state[item.name]
                if item.name in blocked or runtime.disabled:
                    continue
                cooldown = runtime.cooldown_until
                if cooldown is not None and cooldown > current:
                    continue
                runtime.cooldown_until = None
                runtime.last_used_at = current
                return item
        return None

    def record_failure(self, name: str, category: str, *, retry_after: float = 60.0, now: datetime | None = None) -> bool:
        """Return True only when credential-scoped failover is appropriate."""
        if category == "zen_free_quota":
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock:
            runtime = self._state[name]
            runtime.failures += 1
            runtime.consecutive_failures += 1
            runtime.last_used_at = current
            if category in {"auth", "invalid_credential"}:
                runtime.disabled = True
                return True
            if category in {"credential_quota", "credential_rate_limit"}:
                backoff = min(3600.0, 30.0 * (2 ** min(runtime.consecutive_failures - 1, 7)))
                runtime.cooldown_until = current + timedelta(seconds=max(1.0, retry_after, backoff))
                return True
        return False

    def record_success(self, name: str, *, now: datetime | None = None) -> None:\n        current = (now or datetime.now(UTC)).astimezone(UTC)\n        with self._lock:\n            runtime = self._state[name]\n            runtime.successes += 1\n            runtime.consecutive_failures = 0\n            runtime.cooldown_until = None\n            runtime.last_used_at = current\n\n    def enable(self, name: str) -> None:\n        with self._lock:\n            runtime = self._state[name]\n            runtime.disabled = False\n            runtime.consecutive_failures = 0\n            runtime.cooldown_until = None\n\n    def snapshots(self, *, now: datetime | None = None) -> tuple[CredentialSnapshot, ...]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        result: list[CredentialSnapshot] = []
        with self._lock:
            for item in self._credentials:
                runtime = self._state[item.name]
                cooldown = runtime.cooldown_until
                if runtime.disabled:
                    state = "disabled"
                elif cooldown is not None and cooldown > current:
                    state = "cooldown"
                else:
                    state = "ready"
                    cooldown = None
                result.append(CredentialSnapshot(item.name, item.fingerprint, state, cooldown, runtime.successes, runtime.failures, runtime.consecutive_failures, runtime.last_used_at))
        return tuple(result)


def parse_credential_pool(value: str | None) -> tuple[OpenCodeCredential, ...]:
    """Parse name=secret entries separated by newlines or semicolons."""
    if not value or not value.strip():
        return ()
    credentials: list[OpenCodeCredential] = []
    normalized = value.replace("\r\n", "\n").replace(";", "\n")
    for raw in normalized.splitlines():
        entry = raw.strip()
        if not entry:
            continue
        name, separator, secret = entry.partition("=")
        if not separator or not name.strip() or not secret.strip():
            raise ValueError("OPENCODE_CREDENTIALS entries must use name=secret")
        credentials.append(OpenCodeCredential(name.strip(), secret.strip()))
    return tuple(credentials)
