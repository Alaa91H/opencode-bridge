"""Automatic OpenCode model reconciliation and daily free-agent research."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_scout import parse_research_model, primary_agent_ids, research_prompt, select_primary_agent
from model_catalog import best_zen_general_model_id, ranked_zen_general_model_ids
from opencode_client import extract_text_response

log = logging.getLogger("opencode_bridge.model_manager")
BUSY_SESSION_STATES = {"busy", "running", "working", "processing", "generating"}
UTC = timezone.utc
SCOUT_STATE_PATH = Path(__file__).resolve().parent / "runtime" / "agent-scout.json"


class ModelManager:
    """Keep all bridge users on the strongest verified zero-cost OpenCode setup.

    Fast catalog reconciliation keeps models available. A separate daily scout
    researches the current free candidates through OpenCode web tools, validates
    the answer against the live zero-cost allow-list, and then makes the chosen
    primary agent/model the global default for every bridge user and conversation.
    """

    def __init__(
        self,
        client: Any,
        store: Any,
        audit: Any,
        fallback_model: str,
        sync_seconds: float = 900.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        pin_default_model: bool | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.audit = audit
        self.fallback_model = fallback_model
        self.configured_model = fallback_model
        if pin_default_model is None:
            self.pin_default_model = os.environ.get("OPENCODE_PIN_DEFAULT_MODEL", "0").strip().lower() not in {"0", "false", "no", "off"}
        else:
            self.pin_default_model = bool(pin_default_model)
        self.sync_seconds = max(60.0, float(sync_seconds))
        self.scout_interval_seconds = max(3600.0, float(os.environ.get("AGENT_SCOUT_INTERVAL_SECONDS", "86400")))
        self.scout_preferred_agent = os.environ.get("AGENT_SCOUT_PREFERRED_AGENT", "development-agent").strip() or "development-agent"
        self.scout_web_research = os.environ.get("AGENT_SCOUT_WEB_RESEARCH", "1").strip().lower() not in {"0", "false", "no", "off"}
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()
        self._scout_lock = asyncio.Lock()
        self._last_best: str | None = None
        self._preferred_model: str | None = self.configured_model if self.pin_default_model else None
        self._preferred_agent: str | None = None

    @property
    def preferred_model(self) -> str | None:
        return self._preferred_model

    @property
    def preferred_agent(self) -> str | None:
        return self._preferred_agent

    def set_preferred_model(self, model_id: str | None) -> None:
        self._preferred_model = model_id.strip() if isinstance(model_id, str) and model_id.strip() else None

    def current_agent(self, fallback: str) -> str:
        return self._preferred_agent or fallback

    def _apply_live_defaults(self, agent: str, model: str) -> None:
        self._preferred_agent = agent
        self.set_preferred_model(model)
        self.fallback_model = model
        # bot.py is already fully imported when ModelManager.start() runs. Updating
        # these two module globals changes the next prompt for existing sessions,
        # queued tasks, new tasks, and every allowed Telegram user without a restart.
        core = sys.modules.get("bot")
        if core is not None:
            setattr(core, "DEFAULT_AGENT", agent)
            setattr(core, "DEFAULT_MODEL", model)

    async def best_available(self, excluded_ids: set[str] | None = None) -> str | None:
        providers = await self.client.list_providers()
        excluded = excluded_ids or set()
        ranked = ranked_zen_general_model_ids(providers)
        if self.pin_default_model and self.configured_model in ranked and self.configured_model not in excluded:
            return self.configured_model
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

        A request already generating cannot be rewritten mid-response, but the
        saved OpenCode session is updated immediately so its next turn and every
        queued/new task use the selected model.
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

    def _load_scout_state(self) -> dict[str, Any]:
        try:
            value = json.loads(SCOUT_STATE_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_scout_state(self, payload: dict[str, Any]) -> None:
        SCOUT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SCOUT_STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(SCOUT_STATE_PATH)

    @staticmethod
    def _state_time(state: dict[str, Any]) -> datetime | None:
        raw = state.get("last_success_at")
        if not isinstance(raw, str):
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def _scout_due(self) -> bool:
        last = self._state_time(self._load_scout_state())
        return last is None or (datetime.now(UTC) - last).total_seconds() >= self.scout_interval_seconds

    async def _research_free_model(self, agent: str, models: list[str]) -> str | None:
        if not self.scout_web_research or len(models) < 2:
            return None
        session_id: str | None = None
        try:
            created = await self.client.create_session(title="Daily free OpenCode agent scout")
            raw_id = created.get("id") or created.get("sessionId")
            if not isinstance(raw_id, str) or not raw_id:
                return None
            session_id = raw_id
            response = await self.client.send_prompt(
                session_id,
                research_prompt(models),
                model=models[0],
                agent=agent,
            )
            return parse_research_model(extract_text_response(response), models)
        except Exception as exc:
            log.info("تعذر البحث اليومي عن أفضل نموذج مجاني: %s", type(exc).__name__)
            return None
        finally:
            if session_id:
                try:
                    await self.client.delete_session(session_id)
                except Exception:
                    pass

    async def scout_once(self) -> dict[str, Any] | None:
        """Research, validate, persist, and globally apply today's free default."""
        async with self._scout_lock:
            try:
                agents, providers = await asyncio.gather(self.client.list_agents(), self.client.list_providers())
            except Exception as exc:
                log.info("تعذر تشغيل Agent Scout اليومي: %s", type(exc).__name__)
                return None
            agent = select_primary_agent(agents, self.scout_preferred_agent)
            free_models = ranked_zen_general_model_ids(providers)
            if agent is None or not free_models:
                self.audit.write(
                    "daily_agent_scout",
                    "no_candidate",
                    details={"primary_agents": primary_agent_ids(agents), "free_model_count": len(free_models)},
                )
                return None

            pinned = self.configured_model if self.pin_default_model and self.configured_model in free_models else None
            researched = None if pinned else await self._research_free_model(agent, free_models)
            model = pinned or (researched if researched in free_models else free_models[0])
            previous = self._load_scout_state()
            previous_agent = previous.get("selected_agent")
            previous_model = previous.get("selected_model")

            self._apply_live_defaults(agent, model)
            changed_sessions, failed_sessions = await self.force_all_sessions(model)
            now = datetime.now(UTC)
            payload = {
                "last_success_at": now.isoformat(),
                "next_due_at": datetime.fromtimestamp(now.timestamp() + self.scout_interval_seconds, tz=UTC).isoformat(),
                "selected_agent": agent,
                "selected_model": model,
                "selection_method": "pinned_default" if pinned else ("web_research" if researched else "live_catalog_fallback"),
                "previous_agent": previous_agent,
                "previous_model": previous_model,
                "primary_agents": primary_agent_ids(agents),
                "free_models": free_models,
                "sessions_changed": changed_sessions,
                "sessions_failed": failed_sessions,
            }
            self._save_scout_state(payload)
            self.audit.write(
                "daily_agent_scout",
                "selected",
                details={
                    "agent": agent,
                    "model": model,
                    "method": payload["selection_method"],
                    "previous_agent": previous_agent,
                    "previous_model": previous_model,
                    "sessions_changed": changed_sessions,
                    "sessions_failed": failed_sessions,
                },
            )
            log.info("Daily Agent Scout selected agent=%s model=%s", agent, model)
            return payload

    async def _restore_scout_state(self) -> bool:
        state = self._load_scout_state()
        agent = state.get("selected_agent")
        model = state.get("selected_model")
        if not isinstance(agent, str) or not isinstance(model, str):
            return False
        try:
            agents, providers = await asyncio.gather(self.client.list_agents(), self.client.list_providers())
        except Exception:
            return False
        ranked = ranked_zen_general_model_ids(providers)
        if agent not in primary_agent_ids(agents):
            return False
        if self.pin_default_model and self.configured_model in ranked:
            self._apply_live_defaults(agent, self.configured_model)
            return True
        if model not in ranked:
            return False
        self._apply_live_defaults(agent, model)
        return True

    async def reconcile_once(self) -> str | None:
        """Move only idle saved sessions to the current preferred free model."""
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
        await self._restore_scout_state()
        await self.reconcile_once()
        if self._scout_due():
            await self.scout_once()
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
                if self._stopped.is_set():
                    continue
                await self.reconcile_once()
                if self._scout_due():
                    await self.scout_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("توقفت دورة مزامنة النماذج مؤقتًا: %s", type(exc).__name__)
