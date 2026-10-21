"""Application service exposing safe effective configuration views."""

from __future__ import annotations

from typing import Any


class ConfigurationService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def public_config(self, owner_id: str | None = None) -> dict[str, Any]:
        return self.settings.public_dict(owner_id)

    def effective_limits(self, owner_id: str | None = None) -> dict[str, Any]:
        return self.settings.limits_dict(owner_id)
