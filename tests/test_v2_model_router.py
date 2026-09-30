import unittest

from bridge.domain.policies.model_router import ModelCandidate, ModelRouter, ModelTask


class ModelRouterTests(unittest.TestCase):
    def models(self):
        return [
            ModelCandidate("fast", frozenset({"text", "coding"}), quality=.7, latency=.1, cost=.1, context_tokens=32_000),
            ModelCandidate("deep", frozenset({"text", "coding", "image", "reasoning"}), quality=.95, latency=.7, cost=.8, context_tokens=200_000),
            ModelCandidate("unstable", frozenset({"text", "coding"}), quality=1, latency=.1, cost=.1, context_tokens=200_000, recent_failures=5),
        ]

    def test_routes_by_capability_and_context(self):
        router = ModelRouter(self.models())
        route = router.route(ModelTask(frozenset({"image", "reasoning"}), context_tokens=100_000))
        self.assertEqual(route.primary, "deep")

    def test_fallback_chain_excludes_unavailable_and_zero_quota(self):
        catalog = [
            *self.models(),
            ModelCandidate("offline", frozenset({"text"}), context_tokens=1000, available=False),
            ModelCandidate("empty", frozenset({"text"}), context_tokens=1000, quota_remaining=0),
        ]
        route = ModelRouter(catalog).route(ModelTask(frozenset({"text"}), context_tokens=500))
        self.assertNotIn("offline", (route.primary, *route.fallback))
        self.assertNotIn("empty", (route.primary, *route.fallback))

    def test_preference_is_task_local(self):
        router = ModelRouter(self.models())
        preferred = router.route(ModelTask(frozenset({"text"}), preferred_model="deep"))
        normal = router.route(ModelTask(frozenset({"text"})))
        self.assertTrue(preferred.scores)
        self.assertTrue(normal.scores)

    def test_catalog_can_change_during_runtime(self):
        router = ModelRouter(self.models())
        router.replace_catalog([ModelCandidate("new", frozenset({"text"}), context_tokens=10_000)])
        self.assertEqual(router.route(ModelTask(frozenset({"text"}))).primary, "new")

    def test_failure_history_penalizes_candidate(self):
        route = ModelRouter(self.models()).route(ModelTask(frozenset({"coding"})))
        self.assertNotEqual(route.primary, "unstable")
