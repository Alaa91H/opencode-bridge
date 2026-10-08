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

    def record_success(self, name: str, *, now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self._lock:
            runtime = self._state[name]
            runtime.successes += 1
            runtime.consecutive_failures = 0
            runtime.cooldown_until = None
            runtime.last_used_at = current

    def enable(self, name: str) -> None:
        with self._lock:
            runtime = self._state[name]
            runtime.disabled = False
            runtime.consecutive_failures = 0
            runtime.cooldown_until = None

    def snapshots(self, *, now: datetime | None = None) -> tuple[CredentialSnapshot, ...]:
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


    def export_state(self) -> dict:
        """Export runtime metadata only; credential secrets are never persisted."""
        with self._lock:
            return {
                "version": 1,
                "credentials": {
                    name: {
                        "successes": state.successes,
                        "failures": state.failures,
                        "consecutive_failures": state.consecutive_failures,
                        "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None,
                        "last_used_at": state.last_used_at.isoformat() if state.last_used_at else None,
                        "disabled": state.disabled,
                    }
                    for name, state in self._state.items()
                },
            }

    def import_state(self, payload: dict) -> None:
        if payload.get("version") != 1 or not isinstance(payload.get("credentials"), dict):
            return
        with self._lock:
            for name, raw in payload["credentials"].items():
                if name not in self._state or not isinstance(raw, dict):
                    continue
                state = self._state[name]
                state.successes = max(0, int(raw.get("successes", 0)))
                state.failures = max(0, int(raw.get("failures", 0)))
                state.consecutive_failures = max(0, int(raw.get("consecutive_failures", 0)))
                state.disabled = bool(raw.get("disabled", False))
                state.cooldown_until = _parse_datetime(raw.get("cooldown_until"))
                state.last_used_at = _parse_datetime(raw.get("last_used_at"))

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.export_state(), sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def load_state(self, path: Path) -> None:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self.import_state(payload)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_credential_pool(value: str | None) -> tuple[OpenCodeCredential, ...]:
    """Parse name=secret entries separated by newlines or semicolons."""
    if not value or not value.strip():
        return ()
    credentials: list[OpenCodeCredential] = []
    normalized = value.replace("\\r\\n", "\\n").replace(";", "\\n")
    for raw in normalized.splitlines():
        entry = raw.strip()
        if not entry:
            continue
        name, separator, secret = entry.partition("=")
        if not separator or not name.strip() or not secret.strip():
            raise ValueError("OPENCODE_CREDENTIALS entries must use name=secret")
        credentials.append(OpenCodeCredential(name.strip(), secret.strip()))
    return tuple(credentials)
