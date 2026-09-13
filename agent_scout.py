"""Selection helpers for the daily OpenCode agent/model scout.

OpenCode agents are configuration profiles and are not billed independently;
the free/paid property belongs to the model. The runtime therefore selects a
primary agent and then limits model research to the live zero-cost Zen catalog.
"""

from __future__ import annotations

import json
from typing import Any


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
    """Select the best primary agent for this autonomous development bridge.

    The repository-specific development agent is preferred because it carries
    the project's GitHub, CI, workspace-isolation, and no-local-build policies.
    OpenCode's built-in Build agent is the safe fallback; Plan is intentionally
    last because it is designed for analysis rather than autonomous edits.
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
    """Accept only an exact model ID from the zero-cost allow-list."""
    allowed = set(allowed_models)
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


def research_prompt(models: list[str]) -> str:
    """Build a constrained daily research prompt from live zero-cost candidates."""
    candidates = "\n".join(f"- {model}" for model in models[:20])
    return (
        "Perform a fresh web research pass to select the strongest CURRENTLY FREE model for an autonomous "
        "OpenCode software-development agent. Search recent official OpenCode documentation first, then recent "
        "reputable coding-agent benchmarks when available. Prioritize agentic coding, tool use, debugging, "
        "large-repository reasoning, reliability, and Git/GitHub workflows. Ignore paid models completely. "
        "Choose exactly one ID from this live zero-cost allow-list and do not invent a model:\n"
        f"{candidates}\n\n"
        "Return only one JSON object with no markdown: "
        '{"model":"provider/model-id","reason":"short evidence-based reason"}'
    )
