import tempfile
import unittest
from pathlib import Path

from bridge.domain.policies.user_preferences import UserPreferences
from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.infrastructure.database.user_settings_store import UserSettingsStore
from bridge.services.user_preferences_service import UserPreferencesService


class UserPreferencesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = BridgeDatabase(Path(self.tmp.name) / "prefs.db")
        await MigrationRunner(self.db).migrate()
        self.service = UserPreferencesService(UserSettingsStore(self.db))

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    def test_all_required_fields_exist(self):
        value = UserPreferences()
        self.assertEqual(set(value.to_dict()), {
            "timezone", "language", "notification_level", "default_workspace",
            "default_execution_profile", "model_preference", "output_style",
            "retention_days", "schedule_defaults",
        })

    async def test_persistence_and_owner_isolation(self):
        await self.service.update(
            "alice", timezone="Europe/Berlin", language="de",
            notification_level="verbose", default_workspace="repo-a",
            default_execution_profile="DEVELOPMENT", model_preference="model-x",
            output_style="full", retention_days=90,
            schedule_defaults={"misfire": "coalesce"})
        alice = await self.service.get("alice")
        bob = await self.service.get("bob")
        self.assertEqual(alice.timezone, "Europe/Berlin")
        self.assertEqual(alice.schedule_defaults["misfire"], "coalesce")
        self.assertEqual(bob, UserPreferences())

    async def test_restart_persistence(self):
        await self.service.update("alice", language="ar")
        await self.db.close()
        self.db = BridgeDatabase(Path(self.tmp.name) / "prefs.db")
        await MigrationRunner(self.db).migrate()
        self.service = UserPreferencesService(UserSettingsStore(self.db))
        self.assertEqual((await self.service.get("alice")).language, "ar")

    async def test_validation_rejects_bad_values_and_unknown_keys(self):
        with self.assertRaises(Exception):
            await self.service.update("alice", timezone="Not/AZone")
        with self.assertRaises(ValueError):
            await self.service.update("alice", retention_days=-1)
        with self.assertRaises(ValueError):
            await self.service.update("alice", imaginary=True)
