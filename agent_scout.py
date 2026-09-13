"""Daily OpenCode agent/model scout with live global default switching.

OpenCode agents are configuration profiles and do not have a price themselves;
the paid/free dimension belongs to the model.  The scout therefore validates the
best primary agent for this bridge and researches only models that the live
OpenCode provider catalog explicitly reports as zero-cost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from model_catalog import ranked_zen_general_model_ids
from opencode_client import extract_text_response

log = logging.getLogger("opencode_bridge.agent_scout")
UTC = timezone.utc
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60


def _agent_id(agent: dict[str, Any]) -> str | None:
    value = agent.get("name") or agent.get("id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def primary_agent_ids(agents: list[dict[str, Any]]) -> list[str]:
    """Return visible primary-capable OpenCode agents in deterministic order."""
    results: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("hidden") is True:
            continue
        name = _agent_id(agent)
        if not name:
            continue
        mode = str(agent.get("mode") or "primary").casefold()
        if mode not in {"primary", "all"}:
            continue
        results.add(name)
    return sorted(results, key=str.casefold)


def select_primary_agent(agents: list[dict[str, Any]], preferred: str = "development-agent") -> str | None:
    """Select the strongest safe primary agent for this development bridge.

    The repository-specific development agent wins when present because it keeps
    the direct-to-main, CI, workspace, and no-local-build operating policy.  The
    built-in Build agent is the fallback because OpenCode documents it as the
    unrestricted primary development agent. Plan is intentionally last because
    it is designed for analysis rather than autonomous edits.
    """
    available = primary_agent_ids(agents)
    if not available:
        return None
    by_fold = {value.casefold(): value for value in available}
    if preferred.casefold() in by_fold:
        return by_fold[preferred.casefold()]
    if "build" in by_fold:
        return by_fold["build"]
    non_plan = [value for value in available if value.casefold() != "plan"]
    return non_plan[0] if non_plan else available[0]


def parse_research_model(text: str, allowed_models: list[str]) -> str | None:
    """Extract a strict model choice from a research response."""
    allowed = set(allowed_models)
    for candidate in allowed_models:
        if re.search(rf"(?<![\w.-]){re.escape(candidate)}(?![\w.-])", text):
            # Prefer explicit JSON below, but a unique exact ID is still safe.
            pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            model = payload.get("model")
            if isinstance(model, str) and model in allowed:
                return model
    exact_hits = [model for model in allowed_models if model in text]
    return exact_hits[0] if len(exact_hits) == 1 else None


class DailyAgentScout:
    """Research and apply the best currently available free OpenCode default."""

    def __init__(
        self,
        client: Any,
        model_manager: Any,
        audit: Any,
        state_path: Path,
        apply_defaults: Callable[[str, str], None],
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        preferred_agent: str = "development-agent",
        research_enabled: bool = True,
    ) -> None:
        self.client = client
        self.model_manager = model_manager
        self.audit = audit
        self.state_path = state_path
        self.apply_defaults = apply_defaults
        self.interval_seconds = max(3600.0, float(interval_seconds))
        self.preferred_agent = preferred_agent
        self.research_enabled = bool(research_enabled)
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)

    @staticmethod
    def _last_success(state: dict[str, Any]) -> datetime | None:
        raw = state.get("last_success_at")
        if not isinstance(raw, str):
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    async def _research_model(self, agent: str, models: list[str]) -> str | None:
        if not self.research_enabled or len(models) < 2:
            return None
        session_id: str | None = None
        try:
            created = await self.client.create_session(title="Daily free OpenCode agent scout")
            raw_id = created.get("id") or created.get("sessionId")
            if not isinstance(raw_id, str) or not raw_id:
                return None
            session_id = raw_id
            candidates = "\n".join(f"- {model}" for model in models[:20])
            prompt = (
                "Perform a fresh web research pass to select the strongest CURRENTLY FREE model for an autonomous "
                "OpenCode software-development agent. Search recent official OpenCode documentation first, then recent "
                "reputable coding-agent benchmarks when available. Prioritize real agentic coding, tool use, debugging, "
                "large-repository reasoning, reliability, and Git/GitHub workflows. Ignore paid models completely. "
                "You MUST choose exactly one ID from this live zero-cost allow-list and must not invent a model:\n"
                f"{candidates}\n\n"
                "Return only one JSON object with this schema and no markdown: "
                '{"model":"provider/model-id","reason":"short evidence-based reason"}'
            )
            response = await self.client.send_prompt(
                session_id,
                prompt,
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

    async def run_once(self) -> dict[str, Any] | None:
        async with self._lock:
            try:
                agents = await self.client.list_agents()
                providers = await self.client.list_providers()
            except Exception as exc:
                log.warning("تعذر قراءة OpenCode لتشغيل Agent Scout: %s", type(exc).__name__)
                return None

            agent = select_primary_agent(agents, self.preferred_agent)
            free_models = ranked_zen_general_model_ids(providers)
            if agent is None or not free_models:
                self.audit.write(
                    "daily_agent_scout",
                    "no_candidate",
                    details={"primary_agents": primary_agent_ids(agents), "free_model_count": len(free_models)},
                )
                return None

            researched = await self._research_model(agent, free_models)
            model = researched if researched in free_models else free_models[0]
            previous = self._load_state()
            previous_agent = previous.get("selected_agent")
            previous_model = previous.get("selected_model")

            self.apply_defaults(agent, model)
            self.model_manager.set_preferred_model(model)
            changed_sessions, failed_sessions = await self.model_manager.force_all_sessions(model)

            now = datetime.now(UTC)
            payload = {
                "last_success_at": now.isoformat(),
                "next_due_at": (now + timedelta(seconds=self.interval_seconds)).isoformat(),
                "selected_agent": agent,
                "selected_model": model,
                "selection_method": "web_research" if researched else "live_catalog_fallback",
                "previous_agent": previous_agent,
                "previous_model": previous_model,
                "primary_agents": primary_agent_ids(agents),
                "free_models": free_models,
                "sessions_changed": changed_sessions,
                "sessions_failed": failed_sessions,
            }
            self._save_state(payload)
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

    async def _apply_saved_if_valid(self) -> bool:
        state = self._load_state()
        agent = state.get("selected_agent")
        model = state.get("selected_model")
        if not isinstance(agent, str) or not isinstance(model, str):
            return False
        try:
            agents = await self.client.list_agents()
            providers = await self.client.list_providers()
        except Exception:
            return False
        if agent not in primary_agent_ids(agents) or model not in ranked_zen_general_model_ids(providers):
            return False
        self.apply_defaults(agent, model)
        self.model_manager.set_preferred_model(model)
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        state = self._load_state()
        valid_saved = await self._apply_saved_if_valid()
        last_success = self._last_success(state) if valid_saved else None
        due = last_success is None or (datetime.now(UTC) - last_success).total_seconds() >= self.interval_seconds
        if due:
            await self.run_once()
        self._task = asyncio.create_task(self._run(), name="opencode-bridge-daily-agent-scout")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while not self._stopped.is_set():
            state = self._load_state()
            last_success = self._last_success(state)
            elapsed = (datetime.now(UTC) - last_success).total_seconds() if last_success else self.interval_seconds
            delay = max(60.0, self.interval_seconds - elapsed)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                continue
            except asyncio.TimeoutError:
                pass
            if not self._stopped.is_set():
                await self.run_once()
