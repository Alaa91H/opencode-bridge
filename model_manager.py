"""Automatic selection and safe reconciliation of OpenCode Zen models."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from model_catalog import best_zen_general_model_id, ranked_zen_general_model_ids

log = logging.getLogger("opencode_bridge.model_manager")
BUSY_SESSION_STATES = {"busy", "running", "working", "processing", "generating"}


class ModelManager:
    """Keep Telegram sessions on the best active free OpenCode Zen model.

    The daily agent scout may pin a preferred zero-cost model after its research
    pass. The pin is accepted only while that model is still present in the live
    active free catalog, so stale scout state can never force a paid model.
    """

    def __init__(
        self,
        client: Any,
        store: Any,
        audit: Any,
        fallback_model: str,
        sync_seconds: float = 900.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.store = store
        self.audit = audit
        self.fallback_model = fallback_model
        self.sync_seconds = max(60.0, float(sync_seconds))
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_best: str | None = None
        self._preferred_model: str | None = None

    @property
    def preferred_model(self) -> str | None:
        return self._preferred_model

    def set_preferred_model(self, model_id: str | None) -> None:
        self._preferred_model = model_id.strip() if isinstance(model_id, str) and model_id.strip() else None

    async def best_available(self, excluded_ids: set[str] | None = None) -> str | None:
        providers = await self.client.list_providers()
        excluded = excluded_ids or set()
        ranked = ranked_zen_general_model_ids(providers)
        preferred = self._preferred_model
        if preferred and preferred in ranked and preferred not in excluded:
            return preferred
        return best_zen_general_model_id(providers, excluded_ids=excluded)

    async def ensure_session_model(
        self,
        telegram_user_id: str,
        session_id: str,
        current_model: str | None,
        excluded_ids: set[str] | None = None,
    ) -> str:
        """Choose and persist the current best model before executing a task."""
        selected = await self.best_available(excluded_ids=excluded_ids)
        if selected is None:
            return current_model or self.fallback_model
        if selected == current_model:
            return selected
        await self.client.update_session(session_id, model=selected)
        await self.store.update_session(telegram_user_id, model=selected)
        self.audit.write(
            "model_auto_switched",
            "changed",
            actor_id=telegram_user_id,
            details={"from_model": current_model, "to_model": selected, "reason": "catalog_best_general"},
        )
        return selected

    async def force_all_sessions(self, model_id: str) -> tuple[int, int]:
        """Apply one verified free model to every saved conversation immediately.

        An already-running inference cannot be rewritten mid-response, but the
        OpenCode session and bridge state are updated immediately so its next
        turn, every queued task, and every new task use the selected model.
        """
        providers = await self.client.list_providers()
        if model_id not in ranked_zen_general_model_ids(providers):
            raise ValueError("Scout-selected model is not an active zero-cost OpenCode Zen model")
        self.set_preferred_model(model_id)
        sessions = await self.store.list_sessions()
        changed = 0
        failed = 0
        for session in sessions:
            if session.model == model_id:
                continue
            try:
                await self.client.update_session(session.opencode_session_id, model=model_id)
                await self.store.update_session(session.telegram_user_id, model=model_id)
                changed += 1
                self.audit.write(
                    "model_auto_switched",
                    "changed",
                    actor_id=session.telegram_user_id,
                    details={"from_model": session.model, "to_model": model_id, "reason": "daily_agent_scout"},
                )
            except Exception as exc:
                failed += 1
                log.info("تعذر تطبيق نموذج الوكيل اليومي على جلسة %s: %s", session.telegram_user_id, type(exc).__name__)
        self.fallback_model = model_id
        self._last_best = model_id
        return changed, failed

    async def reconcile_once(self) -> str | None:
        """Move only idle saved sessions to the best available model."""
        async with self._lock:
            try:
                selected = await self.best_available()
            except Exception as exc:
                log.info("تعذر تحديث كتالوج النماذج: %s", type(exc).__name__)
                return None
            if selected is None:
                log.warning("كتالوج OpenCode Zen لا يحتوي نموذجًا مجانيًا نشطًا للتحويل التلقائي.")
                return None
            if selected != self._last_best:
                self.audit.write(
                    "model_catalog_reconciled",
                    "selected",
                    details={"best_general_model": selected, "previous_best_model": self._last_best},
                )
                self._last_best = selected
            try:
                states = await self.client.get_session_status()
                sessions = await self.store.list_sessions()
            except Exception as exc:
                log.info("تعذر قراءة الجلسات لمزامنة النموذج: %s", type(exc).__name__)
                return selected
            for session in sessions:
                if session.model == selected:
                    continue
                state = ""
                if isinstance(states, dict):
                    raw = states.get(session.opencode_session_id, {})
                    state = str(raw.get("state") or "").casefold() if isinstance(raw, dict) else ""
                if state in BUSY_SESSION_STATES:
                    continue
                try:
                    await self.client.update_session(session.opencode_session_id, model=selected)
                    await self.store.update_session(session.telegram_user_id, model=selected)
                    self.audit.write(
                        "model_auto_switched",
                        "changed",
                        actor_id=session.telegram_user_id,
                        details={"from_model": session.model, "to_model": selected, "reason": "catalog_reconciliation"},
                    )
                except Exception as exc:
                    log.info("تعذر تبديل نموذج جلسة %s: %s", session.telegram_user_id, type(exc).__name__)
            return selected

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        await self.reconcile_once()
        self._task = asyncio.create_task(self._run(), name="opencode-bridge-model-catalog")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._sleep(self.sync_seconds)
                if not self._stopped.is_set():
                    await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("توقفت دورة مزامنة النماذج مؤقتًا: %s", type(exc).__name__)
