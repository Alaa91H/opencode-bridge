"""Choose the agent backend for an owner without changing OpenCode defaults.

``resolve`` returns ``None`` for OpenCode, which keeps every legacy code path
exactly as it was. A non-None value is an ``AgentBackend`` the service layer
delegates to.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bridge.infrastructure.database.backend_sessions import (
    ALLOWED_BACKENDS,
    BackendSessionStore,
)

DEFAULT_HEALTH_TTL_SECONDS = 60.0


class BackendRouter:
    def __init__(
        self,
        *,
        default_mode: str,
        opencode: Any,
        freebuff: Any,
        sessions: BackendSessionStore,
        logger: logging.Logger | None = None,
        health_ttl_seconds: float = DEFAULT_HEALTH_TTL_SECONDS,
    ) -> None:
        mode = str(default_mode or "opencode").strip().lower()
        if mode not in ALLOWED_BACKENDS:
            raise ValueError("AGENT_BACKEND must be opencode, freebuff or auto")
        self.default_mode = mode
        self.opencode = opencode
        self.freebuff = freebuff
        self.sessions = sessions
        self.log = logger or logging.getLogger(__name__)
        self.health_ttl_seconds = max(1.0, float(health_ttl_seconds))
        self._health: dict[str, Any] | None = None
        self._health_at = 0.0

    async def mode_for(self, owner_id: str) -> str:
        preference = await self.sessions.get_preference(str(owner_id))
        return preference or self.default_mode

    async def set_mode(self, owner_id: str, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in ALLOWED_BACKENDS:
            raise ValueError("unsupported backend")
        await self.sessions.set_preference(str(owner_id), normalized)

    async def _freebuff_health(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._health is not None and now - self._health_at < self.health_ttl_seconds:
            return self._health
        try:
            health = await self.freebuff.health()
        except Exception as exc:  # a probe must never break a command
            self.log.info("Freebuff health probe failed: %s", type(exc).__name__)
            health = {"healthy": False, "backend": "freebuff", "reason": "probe_failed"}
        self._health = dict(health)
        self._health_at = now
        return self._health

    async def resolve(self, owner_id: str) -> Any | None:
        mode = await self.mode_for(owner_id)
        if mode == "opencode":
            return None
        if mode == "freebuff":
            return self.freebuff
        health = await self._freebuff_health()
        return self.freebuff if health.get("healthy") else None

    async def status(self, owner_id: str) -> dict[str, Any]:
        mode = await self.mode_for(owner_id)
        effective = "opencode" if await self.resolve(owner_id) is None else "freebuff"
        freebuff_health = await self._freebuff_health(force=True)
        return {
            "mode": mode,
            "effective": effective,
            "default_mode": self.default_mode,
            "freebuff": freebuff_health,
            "capabilities": getattr(self.freebuff, "capabilities", None),
        }


BackendResolver = Callable[[str], Awaitable[Any | None]]
