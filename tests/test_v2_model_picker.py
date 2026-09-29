import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase
from bridge.infrastructure.database.user_settings_store import UserSettingsStore
from bridge.services.model_selection_service import ModelSelectionError, ModelSelectionService
from bridge.services.user_preferences_service import UserPreferencesService


def provider_catalog(*models):
    """Build a minimal live OpenCode Zen catalog payload.

    Each entry is ``(model_id, variants, context)``; a larger context window and
    reasoning support make ``strong-free`` outrank ``plain-free`` so index
    ordering in the picker is deterministic and testable.
    """
    return {
        "all": [
            {
                "id": "opencode",
                "models": {
                    model_id: {
                        "status": "active",
                        "cost": {"input": 0, "output": 0},
                        "capabilities": {
                            "toolcall": True,
                            "reasoning": True,
                            "input": {"text": True},
                        },
                        "limit": {"context": context, "output": 64000},
                        "variants": variants,
                    }
                    for model_id, variants, context in models
                },
            }
        ]
    }


STRONG = ("strong-free", {"xhigh": {}, "high": {}, "low": {}, "max": {}, "medium": {}, "minimal": {}}, 1_000_000)
PLAIN = ("plain-free", {}, 100_000)


class FakeSession:
    def __init__(self, model):
        self.opencode_session_id = "ses_test"
        self.model = model


class FakeSessionStore:
    def __init__(self, model=None):
        self.model = model
        self.written = []

    async def get_session(self, owner_id):
        return FakeSession(self.model) if self.model else None

    async def update_session(self, owner_id, session_id, model):
        self.model = model
        self.written.append((owner_id, session_id, model))


class ModelSelectionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = BridgeDatabase(Path(self.tmp.name) / "picker.db")
        await MigrationRunner(self.db).migrate()
        self.preferences = UserPreferencesService(UserSettingsStore(self.db))
        self.catalog = provider_catalog(STRONG, PLAIN)
        self.sessions = FakeSessionStore(model="opencode/plain-free")
        self.variants = {}
        self.service = ModelSelectionService(
            catalog_provider=AsyncMock(return_value=self.catalog),
            preferences=self.preferences,
            session_model_reader=self.sessions.get_session,
            session_model_writer=self.sessions.update_session,
            variant_resolver=AsyncMock(side_effect=lambda owner, model: self.variants.get(owner)),
            audit_write=lambda *args, **kwargs: None,
        )

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_view_lists_ranked_models_with_live_variants(self):
        view = await self.service.view("u1")
        self.assertEqual([choice.model_id for choice in view.choices], ["opencode/strong-free", "opencode/plain-free"])
        self.assertEqual(view.choices[0].variants, ("max", "xhigh", "high", "medium", "low", "minimal"))
        self.assertEqual(view.choices[1].variants, ())
        self.assertEqual(view.current_model, "opencode/plain-free")
        self.assertFalse(view.pinned)

    async def test_selecting_model_persists_pin_and_updates_session(self):
        view = await self.service.select_model("u1", 0)
        self.assertTrue(view.pinned)
        self.assertEqual(view.preferred_model, "opencode/strong-free")
        self.assertEqual(self.sessions.model, "opencode/strong-free")
        self.assertEqual(self.sessions.written, [("u1", "ses_test", "opencode/strong-free")])

    async def test_selecting_variant_persists_and_applies_level(self):
        await self.service.select_model("u1", 0)
        view = await self.service.select_variant("u1", 0, 3)  # index 2 is "high"
        self.assertEqual((await self.preferences.get("u1")).model_variant, "high")
        self.assertTrue((await self.preferences.get("u1")).model_pinned)
        self.assertEqual(view.current_model, "opencode/strong-free")

    async def test_variant_index_zero_means_automatic_level(self):
        await self.service.select_model("u1", 0)
        await self.service.select_variant("u1", 0, 6)  # "low"
        await self.service.select_variant("u1", 0, 0)  # back to automatic
        self.assertIsNone((await self.preferences.get("u1")).model_variant)
        self.assertTrue((await self.preferences.get("u1")).model_pinned)

    async def test_model_without_variants_has_only_automatic_level(self):
        await self.service.select_model("u1", 1)
        view = await self.service.view("u1")
        choice = view.variant_choice(1, 0)
        self.assertIsNotNone(choice)
        self.assertIsNone(choice.variant_id)
        self.assertIsNone(choice.strongest_variant)
        with self.assertRaises(ModelSelectionError):
            await self.service.select_variant("u1", 1, 1)

    async def test_out_of_range_and_removed_models_are_rejected(self):
        with self.assertRaises(ModelSelectionError):
            await self.service.select_model("u1", 99)
        with self.assertRaises(ModelSelectionError):
            await self.service.select_variant("u1", 99, 0)
        with self.assertRaises(ModelSelectionError):
            await self.service.select_variant("u1", 0, 99)

    async def test_auto_clears_pin_and_returns_to_best_catalog_model(self):
        await self.service.select_model("u1", 0)
        self.assertTrue((await self.preferences.get("u1")).model_pinned)
        view = await self.service.auto("u1")
        preferences = await self.preferences.get("u1")
        self.assertFalse(preferences.model_pinned)
        self.assertIsNone(preferences.model_preference)
        self.assertIsNone(preferences.model_variant)
        self.assertEqual(self.sessions.model, "opencode/strong-free")
        self.assertFalse(view.pinned)

    async def test_pin_is_owner_scoped(self):
        await self.service.select_model("u1", 0)
        self.assertEqual(await self.service.pinned_model("u1"), "opencode/strong-free")
        self.assertIsNone(await self.service.pinned_model("u2"))

    async def test_changing_model_keeps_still_valid_variant(self):
        self.catalog = provider_catalog(STRONG, PLAIN)
        await self.service.select_model("u1", 0)
        await self.service.select_variant("u1", 0, 4)  # "medium"
        self.service.catalog_provider = AsyncMock(return_value=self.catalog)
        await self.service.select_model("u1", 0)
        self.assertEqual((await self.preferences.get("u1")).model_variant, "medium")

    async def test_view_reports_resolved_variant(self):
        self.variants["u1"] = "high"
        view = await self.service.view("u1")
        self.assertEqual(view.current_variant, "high")

    async def test_empty_catalog_raises_lookup_style_error(self):
        self.service.catalog_provider = AsyncMock(return_value=provider_catalog())
        view = await self.service.view("u1")
        self.assertEqual(view.choices, ())
        self.assertIsNone(view.model_at(0))
        self.assertIsNone(view.variant_choice(0, 0))
