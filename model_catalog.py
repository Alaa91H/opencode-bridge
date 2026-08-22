"""OpenCode model catalog helpers for safe, deterministic Telegram display."""

from __future__ import annotations

from typing import Any


OPENCODE_ZEN_PROVIDER_ID = "opencode"
ACTIVE_MODEL_STATUSES = {"active", "available", "stable"}


def _numeric_values(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        numbers: list[float] = []
        for child in value.values():
            numbers.extend(_numeric_values(child))
        return numbers
    if isinstance(value, list):
        numbers: list[float] = []
        for child in value:
            numbers.extend(_numeric_values(child))
        return numbers
    return []


def is_zero_cost_model(model: dict[str, Any]) -> bool:
    """Return true only when the provider explicitly reports all model costs as zero.

    Missing or malformed pricing is treated as unknown rather than free, so the
    user-facing catalog never labels a paid or unpriced model as free.
    """
    cost = model.get("cost")
    if not isinstance(cost, dict):
        return False
    values = _numeric_values(cost)
    return bool(values) and all(value == 0 for value in values)


def free_model_ids(provider_data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
    """List available zero-cost provider/model IDs in deterministic order."""
    providers: Any = provider_data.get("all", []) if isinstance(provider_data, dict) else provider_data
    if not isinstance(providers, list):
        return []
    results: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        provider_id = provider.get("id") or provider.get("name")
        models = provider.get("models")
        if not isinstance(provider_id, str) or not isinstance(models, dict):
            continue
        for model_id, model in models.items():
            if isinstance(model_id, str) and isinstance(model, dict) and is_zero_cost_model(model):
                results.add(f"{provider_id}/{model_id}")
    return sorted(results, key=str.casefold)


def _zen_free_models(provider_data: dict[str, Any] | list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    providers: Any = provider_data.get("all", []) if isinstance(provider_data, dict) else provider_data
    if not isinstance(providers, list):
        return []
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("id") != OPENCODE_ZEN_PROVIDER_ID:
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            return []
        return [
            (f"{OPENCODE_ZEN_PROVIDER_ID}/{model_id}", model)
            for model_id, model in models.items()
            if isinstance(model_id, str) and isinstance(model, dict) and is_zero_cost_model(model)
        ]
    return []


def _limit_value(model: dict[str, Any], key: str) -> int:
    limit = model.get("limit")
    value = limit.get(key) if isinstance(limit, dict) else 0
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _general_model_score(model: dict[str, Any]) -> tuple[int, int, int, int]:
    """Score documented general-purpose capabilities, never brand marketing.

    The ordering rewards active zero-cost Zen models that can reliably handle
    tool use, reasoning, rich user attachments, and long context. A secondary
    deterministic tie-breaker is applied by the caller using the model ID.
    """
    status = str(model.get("status") or "").casefold()
    if status and status not in ACTIVE_MODEL_STATUSES:
        return (-1, 0, 0, 0)
    capabilities = model.get("capabilities") if isinstance(model.get("capabilities"), dict) else {}
    inputs = capabilities.get("input") if isinstance(capabilities.get("input"), dict) else {}
    score = 0
    score += 18 if capabilities.get("toolcall") is True else 0
    score += 12 if capabilities.get("reasoning") is True else 0
    score += 14 if capabilities.get("attachment") is True else 0
    score += 6 if inputs.get("text") is True else 0
    score += 8 if inputs.get("image") is True else 0
    score += 10 if inputs.get("pdf") is True else 0
    score += 4 if inputs.get("audio") is True else 0
    score += 4 if inputs.get("video") is True else 0
    context = _limit_value(model, "context")
    output = _limit_value(model, "output")
    score += min(10, context // 100_000)
    score += min(8, output // 32_000)
    return (score, context, output, 1 if status == "active" else 0)


def ranked_zen_general_model_ids(provider_data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
    """Return active, free Zen models ordered for broad Telegram workloads."""
    ranked = [
        (model_id, _general_model_score(model))
        for model_id, model in _zen_free_models(provider_data)
    ]
    ranked = [(model_id, score) for model_id, score in ranked if score[0] >= 0]
    return [
        model_id
        for model_id, _ in sorted(
            ranked,
            key=lambda item: (-item[1][0], -item[1][1], -item[1][2], -item[1][3], item[0].casefold()),
        )
    ]


def best_zen_general_model_id(
    provider_data: dict[str, Any] | list[dict[str, Any]],
    excluded_ids: set[str] | None = None,
) -> str | None:
    """Choose the best currently active free Zen model, optionally excluding failures."""
    excluded = excluded_ids or set()
    return next((model_id for model_id in ranked_zen_general_model_ids(provider_data) if model_id not in excluded), None)


def zen_free_model_ids(provider_data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
    """List only zero-cost models offered by the built-in OpenCode Zen provider."""
    return sorted((model_id for model_id, _ in _zen_free_models(provider_data)), key=str.casefold)
