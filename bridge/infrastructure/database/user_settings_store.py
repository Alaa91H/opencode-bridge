"""Persistence for owner-scoped user settings."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from bridge.domain.policies.user_preferences import UserPreferences
from bridge.infrastructure.database.sqlite import BridgeDatabase


class UserSettingsStore:
    def __init__(self, database: BridgeDatabase) -> None:
        self.database = database

    async def get(self, owner_id: str) -> UserPreferences:
        db = await self.database.connect()
        async with db.execute(
            "SELECT settings_json FROM user_settings WHERE owner_id = ?", (owner_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return UserPreferences()
        return UserPreferences.from_dict(json.loads(str(row["settings_json"])))

    async def set(self, owner_id: str, preferences: UserPreferences) -> None:
        db = await self.database.connect()
        payload = json.dumps(preferences.to_dict(), ensure_ascii=False, sort_keys=True)
        now = datetime.now(UTC).isoformat()
        async with self.database.transaction(immediate=True):
            await db.execute(
                """INSERT INTO user_settings(owner_id,settings_json,updated_at)
                   VALUES (?,?,?)
                   ON CONFLICT(owner_id) DO UPDATE SET
                   settings_json=excluded.settings_json, updated_at=excluded.updated_at""",
                (owner_id, payload, now),
            )
