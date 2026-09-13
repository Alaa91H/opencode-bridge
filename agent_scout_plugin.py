"""Runtime integration for the daily OpenCode agent scout."""

from __future__ import annotations

import os

import agent_scout
import bot as core

_scout: agent_scout.DailyAgentScout | None = None


def _apply_defaults(agent: str, model: str) -> None:
    # OpenCode Bridge passes these values on every prompt, so changing the live
    # defaults affects existing conversations on their next turn as well as all
    # queued and newly-created tasks without restarting the service.
    core.DEFAULT_AGENT = agent
    core.DEFAULT_MODEL = model


async def install() -> None:
    global _scout
    if _scout is not None or core.model_manager is None:
        return
    interval = max(3600, int(os.environ.get("AGENT_SCOUT_INTERVAL_SECONDS", "86400")))
    preferred_agent = os.environ.get("AGENT_SCOUT_PREFERRED_AGENT", "development-agent").strip() or "development-agent"
    research_enabled = os.environ.get("AGENT_SCOUT_WEB_RESEARCH", "1").strip().lower() not in {"0", "false", "no", "off"}
    _scout = agent_scout.DailyAgentScout(
        client=core.client,
        model_manager=core.model_manager,
        audit=core.audit,
        state_path=core.BRIDGE_DIR / "runtime" / "agent-scout.json",
        apply_defaults=_apply_defaults,
        interval_seconds=interval,
        preferred_agent=preferred_agent,
        research_enabled=research_enabled,
    )
    await _scout.start()


async def close() -> None:
    global _scout
    if _scout is None:
        return
    await _scout.stop()
    _scout = None
