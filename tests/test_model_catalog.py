from __future__ import annotations

import unittest

from model_catalog import best_zen_general_model_id, free_model_ids, is_zero_cost_model, ranked_zen_general_model_ids, zen_free_model_ids


class ModelCatalogTests(unittest.TestCase):
    def test_zero_cost_model_requires_explicit_zero_pricing(self) -> None:
        self.assertTrue(is_zero_cost_model({"cost": {"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}}))
        self.assertFalse(is_zero_cost_model({"cost": {"input": 0, "output": 0.01}}))
        self.assertFalse(is_zero_cost_model({"cost": {"input": 0, "output": 0, "cache": {"read": 0.001}}}))
        self.assertFalse(is_zero_cost_model({"name": "Model marked free but without pricing"}))

    def test_catalog_keeps_only_zero_cost_provider_models(self) -> None:
        providers = {
            "all": [
                {
                    "id": "free-provider",
                    "models": {
                        "free-model": {"cost": {"input": 0, "output": 0}},
                        "paid-model": {"cost": {"input": 1, "output": 2}},
                    },
                },
                {
                    "id": "unknown-provider",
                    "models": {"unknown-model": {"name": "Unknown pricing"}},
                },
            ]
        }
        self.assertEqual(free_model_ids(providers), ["free-provider/free-model"])

    def test_general_ranking_prefers_active_rich_capability_model(self) -> None:
        providers = {
            "all": [
                {
                    "id": "opencode",
                    "models": {
                        "text-only": {
                            "status": "active",
                            "cost": {"input": 0, "output": 0},
                            "capabilities": {"input": {"text": True}, "toolcall": True, "reasoning": True},
                            "limit": {"context": 1_000_000, "output": 128_000},
                        },
                        "general-rich": {
                            "status": "active",
                            "cost": {"input": 0, "output": 0},
                            "capabilities": {
                                "attachment": True,
                                "toolcall": True,
                                "reasoning": True,
                                "input": {"text": True, "image": True, "pdf": True, "audio": True, "video": True},
                            },
                            "limit": {"context": 200_000, "output": 64_000},
                        },
                        "retired": {
                            "status": "deprecated",
                            "cost": {"input": 0, "output": 0},
                            "capabilities": {"input": {"text": True}, "toolcall": True, "reasoning": True},
                        },
                    },
                }
            ]
        }
        self.assertEqual(ranked_zen_general_model_ids(providers), ["opencode/general-rich", "opencode/text-only"])
        self.assertEqual(best_zen_general_model_id(providers), "opencode/general-rich")
        self.assertEqual(best_zen_general_model_id(providers, {"opencode/general-rich"}), "opencode/text-only")

    def test_zen_catalog_excludes_free_models_from_other_providers(self) -> None:
        providers = {
            "all": [
                {
                    "id": "other-free-provider",
                    "models": {"free-model": {"cost": {"input": 0, "output": 0}}},
                },
                {
                    "id": "opencode",
                    "name": "OpenCode Zen",
                    "models": {
                        "zen-free": {"cost": {"input": 0, "output": 0, "cache": {"read": 0}}},
                        "zen-paid": {"cost": {"input": 0.1, "output": 0.2}},
                    },
                },
            ]
        }
        self.assertEqual(zen_free_model_ids(providers), ["opencode/zen-free"])


if __name__ == "__main__":
    unittest.main()
