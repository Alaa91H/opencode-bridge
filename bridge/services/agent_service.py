"""OpenCode session/application service with no Telegram dependency."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from model_catalog import ranked_zen_general_model_ids


class SessionRepository(Protocol):
    async def get_session(self, telegram_user_id: str) -> Any | None: ...
    async def create_session(self, telegram_user_id: str, opencode_session_id: str, model: str | None = None) -> Any: ...
    async def delete_session(self, telegram_user_id: str) -> bool: ...


class OpenCodePort(Protocol):
    async def create_session(self, title: str | None = None) -> dict: ...
    async def update_session(self, session_id: str, *, model: str | None = None, title: str | None = None) -> dict: ...
    async def abort_session(self, session_id: str) -> bool: ...
    async def share_session(self, session_id: str) -> str | None: ...
    async def unshare_session(self, session_id: str) -> bool: ...
    async def health(self) -> dict: ...
    async def get_session_status(self) -> dict: ...
    async def list_agents(self) -> list[dict]: ...
    async def list_providers(self) -> Any: ...


class NoActiveSession(LookupError):
    pass


@dataclass(frozen=True)
class ModelOverview:
    current_model: str
    preferred_model: str
    variant: str | None
    available_models: tuple[str, ...]


@dataclass(frozen=True)
class AgentSessionStatus:
    healthy: bool
    version: str
    has_session: bool
    state: str | None
    model: str | None
    variant: str | None
    agent: str


class AgentService:
    def __init__(
        self,
        client: OpenCodePort,
        sessions: SessionRepository,
        *,
        default_model: str,
        default_agent: str,
        default_variant: str | None,
        variant_model: str,
        model_manager_provider: Callable[[], Any | None],
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.sessions = sessions
        self.default_model = default_model
        self.default_agent = default_agent
        self.default_variant = default_variant
        self.variant_model = variant_model
        self.model_manager_provider = model_manager_provider
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def extract_session_id(session: dict) -> str:
        session_id = session.get("id") or session.get("sessionId") or session.get("session", {}).get("id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("استجاب الوكيل دون معرّف جلسة صالح")
        return session_id

    def variant_for_model(self, model_id: str | None) -> str | None:
        manager = self.model_manager_provider()
        if manager is not None:
            dynamic = manager.variant_for_model(model_id)
            if dynamic:
                return dynamic
        if not self.default_variant or not model_id:
            return None
        return self.default_variant if model_id == self.variant_model else None

    async def ensure_session(self, user_id: str) -> str:
        current = await self.sessions.get_session(user_id)
        manager = self.model_manager_provider()
        if current:
            if manager is not None:
                try:
                    await manager.ensure_session_model(
                        user_id,
                        current.opencode_session_id,
                        current.model,
                    )
                except Exception as exc:
                    self.log.info(
                        "تعذر تحديث نموذج الجلسة قبل التنفيذ: %s",
                        type(exc).__name__,
                    )
            return current.opencode_session_id

        created = await self.client.create_session(title=f"جلسة تيليغرام {user_id}")
        session_id = self.extract_session_id(created)
        await self.client.update_session(session_id, model=self.default_model)
        await self.sessions.create_session(user_id, session_id, self.default_model)
        manager = self.model_manager_provider()
        if manager is not None:
            try:
                await manager.ensure_session_model(user_id, session_id, self.default_model)
            except Exception as exc:
                self.log.info(
                    "تعذر اختيار النموذج التلقائي للجلسة الجديدة: %s",
                    type(exc).__name__,
                )
        return session_id

    async def fresh_session(self, user_id: str) -> str:
        existing = await self.sessions.get_session(user_id)
        if existing:
            try:
                await self.client.abort_session(existing.opencode_session_id)
            except Exception as exc:
                self.log.info("تعذر إيقاف الجلسة السابقة قبل الاستبدال: %s", type(exc).__name__)
            await self.sessions.delete_session(user_id)
        return await self.ensure_session(user_id)

    async def abort_current(self, user_id: str) -> bool:
        session = await self.sessions.get_session(user_id)
        return bool(session and await self.client.abort_session(session.opencode_session_id))

    async def share_current(self, user_id: str) -> str | None:
        session = await self.sessions.get_session(user_id)
        if session is None:
            raise NoActiveSession(user_id)
        return await self.client.share_session(session.opencode_session_id)

    async def unshare_current(self, user_id: str) -> bool:
        session = await self.sessions.get_session(user_id)
        if session is None:
            raise NoActiveSession(user_id)
        return await self.client.unshare_session(session.opencode_session_id)

    async def model_overview(self, user_id: str) -> ModelOverview:
        models = ranked_zen_general_model_ids(await self.client.list_providers())
        if not models:
            raise LookupError("no_free_models")
        await self.ensure_session(user_id)
        session = await self.sessions.get_session(user_id)
        current_model = session.model if session and session.model else models[0]
        manager = self.model_manager_provider()
        preferred = (
            manager.preferred_model
            if manager is not None and manager.preferred_model
            else current_model
        )
        return ModelOverview(
            current_model=current_model,
            preferred_model=preferred,
            variant=self.variant_for_model(current_model),
            available_models=tuple(models),
        )

    async def status(self, user_id: str) -> AgentSessionStatus:
        session = await self.sessions.get_session(user_id)
        health = await self.client.health()
        version = str(health.get("version", "غير معروف"))
        healthy = bool(health.get("healthy"))
        if session is None:
            return AgentSessionStatus(
                healthy=healthy,
                version=version,
                has_session=False,
                state=None,
                model=None,
                variant=None,
                agent=self.default_agent,
            )
        states = await self.client.get_session_status()
        state = states.get(session.opencode_session_id, {}).get("state", "غير معروف")
        model = session.model or self.default_model
        return AgentSessionStatus(
            healthy=healthy,
            version=version,
            has_session=True,
            state=str(state),
            model=model,
            variant=self.variant_for_model(model),
            agent=self.default_agent,
        )

    async def health(self) -> dict:
        return await self.client.health()

    async def agents(self) -> list[dict]:
        return await self.client.list_agents()
