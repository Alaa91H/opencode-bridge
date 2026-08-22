"""OpenCode model catalog helpers for safe, deterministic Telegram display."""

from __future__ import annotations

from typing import Any


OPENCODE_ZEN_PROVIDER_ID = "opencode"


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


def zen_free_model_ids(provider_data: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
    """List only zero-cost models offered by the built-in OpenCode Zen provider."""
    providers: Any = provider_data.get("all", []) if isinstance(provider_data, dict) else provider_data
    if not isinstance(providers, list):
        return []
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("id") != OPENCODE_ZEN_PROVIDER_ID:
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            return []
        return sorted(
            [
                f"{OPENCODE_ZEN_PROVIDER_ID}/{model_id}"
                for model_id, model in models.items()
                if isinstance(model_id, str) and isinstance(model, dict) and is_zero_cost_model(model)
            ],
            key=str.casefold,
        )
    return []
