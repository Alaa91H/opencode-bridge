"""Application service for validated user preference updates."""

from __future__ import annotations

from dataclasses import replace

from bridge.domain.policies.user_preferences import UserPreferences
from bridge.infrastructure.database.user_settings_store import UserSettingsStore


class UserPreferencesService:
    def __init__(self, store: UserSettingsStore) -> None:
        self.store = store

    async def get(self, owner_id: str) -> UserPreferences:
        return await self.store.get(owner_id)

    async def update(self, owner_id: str, **changes: object) -> UserPreferences:
        current = await self.get(owner_id)
        unknown = set(changes) - set(UserPreferences.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown preferences: {', '.join(sorted(unknown))}")
        updated = replace(current, **changes)
        await self.store.set(owner_id, updated)
        return updated
