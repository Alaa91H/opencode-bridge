"""Quota-aware OpenCode credential selection.

The pool distinguishes credential-scoped limits from Zen free-model IP limits.
It never rotates credentials for zen_free_quota because that bucket is shared by the host IP upstream.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Iterable


@dataclass(frozen=True)
class OpenCodeCredential:
    name: str
    secret: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.secret.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class CredentialSnapshot:
    name: str
    fingerprint: str
    state: str
    cooldown_until: datetime | None = None


class CredentialPool:
    """Round-robin pool for legitimate credential-scoped failover."""

    def __init__(self, credentials: Iterable[OpenCodeCredential] = ()) -> None:
        self._credentials = tuple(credentials)
        names = [item.name for item in self._credentials]
        if len(names) != len(set(names)):
            raise ValueError("credential names must be unique")
        if any(not item.name or not item.secret for item in self._credentials):
            raise ValueError("credential name and secret must be non-empty")
        self._cooldowns: dict[str, datetime] = {}
        self._disabled: set[str] = set()
        self._cursor = 0
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
                if item.name in blocked or item.name in self._disabled:
                    continue
                cooldown = self._cooldowns.get(item.name)
                if cooldown is not None and cooldown > current:
                    continue
                self._cooldowns.pop(item.name, None)
                self._cursor = (index + 1) % len(self._credentials)
                return item
        return None

    def record_failure(self, name: str, category: str, *, retry_after: float = 60.0, now: datetime | None = None) -> bool:
        """Return True only when credential-scoped failover is appropriate."""
        if category == "zen_free_quota":
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock:
            if category in {"auth", "invalid_credential"}:
                self._disabled.add(name)
                return True
            if category in {"credential_quota", "credential_rate_limit"}:
                self._cooldowns[name] = current + timedelta(seconds=max(1.0, retry_after))
                return True
        return False

    def snapshots(self, *, now: datetime | None = None) -> tuple[CredentialSnapshot, ...]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        result: list[CredentialSnapshot] = []
        with self._lock:
            for item in self._credentials:
                cooldown = self._cooldowns.get(item.name)
                if item.name in self._disabled:
                    state = "disabled"
                elif cooldown is not None and cooldown > current:
                    state = "cooldown"
                else:
                    state = "ready"
                    cooldown = None
                result.append(CredentialSnapshot(item.name, item.fingerprint, state, cooldown))
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
