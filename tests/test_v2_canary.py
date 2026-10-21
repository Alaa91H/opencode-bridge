import unittest

from bridge.services.canary_service import CanaryMetrics, CanaryPolicy, ShadowResult


class CanaryTests(unittest.TestCase):
    def test_shadow_compares_scheduler_without_execution(self):
        good = ShadowResult(("a", "b"), ("a", "b"))
        bad = ShadowResult(("a", "b"), ("a", "c"))
        self.assertTrue(good.matches)
        self.assertFalse(bad.matches)

    def test_task_selection_is_deterministic_and_small(self):
        policy = CanaryPolicy(percentage=5)
        first = [policy.selected(f"task-{i}") for i in range(1000)]
        second = [policy.selected(f"task-{i}") for i in range(1000)]
        self.assertEqual(first, second)
        selected = sum(first)
        self.assertGreater(selected, 20)
        self.assertLess(selected, 90)

    def test_metrics_trigger_automatic_rollback(self):
        policy = CanaryPolicy()
        self.assertFalse(policy.should_rollback(CanaryMetrics(.01, 0, 1.1)))
        self.assertTrue(policy.should_rollback(CanaryMetrics(.03, 0, 1.1)))
        self.assertTrue(policy.should_rollback(CanaryMetrics(.01, .01, 1.1)))
        self.assertTrue(policy.should_rollback(CanaryMetrics(.01, 0, 1.6)))

    def test_gradual_expansion_is_bounded(self):
        policy = CanaryPolicy(percentage=1)
        self.assertEqual(policy.expand(4), 5)
        self.assertEqual(policy.expand(200), 100)
