import unittest

from bridge.domain.policies.dynamic_limits import DynamicLimits, ResourceBudget


class DynamicLimitsTests(unittest.TestCase):
    def setUp(self):
        self.budget = ResourceBudget(
            available_memory_bytes=4 * 1024**3,
            available_disk_bytes=100 * 1024**3,
            cpu_count=4)

    def test_zero_means_unlimited_by_application_policy(self):
        limits = DynamicLimits(pending_seconds=0, output_count=0)
        self.assertTrue(limits.allows("output_count", 1_000_000, self.budget))
        self.assertEqual(limits.resolve("pending_seconds", self.budget), 0)

    def test_auto_is_resource_calculated(self):
        limits = DynamicLimits()
        self.assertGreater(limits.resolve("workers", self.budget), 0)
        self.assertGreater(limits.resolve("total_attachment_bytes", self.budget), 0)
        self.assertGreater(limits.resolve("attachment_count", self.budget), 0)

    def test_finite_legacy_values_remain_compatible(self):
        limits = DynamicLimits(attachment_count=10, total_attachment_bytes=1000, workers=3)
        self.assertTrue(limits.allows("attachment_count", 10, self.budget))
        self.assertFalse(limits.allows("attachment_count", 11, self.budget))
        self.assertEqual(limits.resolve("workers", self.budget), 3)

    def test_resource_guard_still_bounds_auto(self):
        tiny = ResourceBudget(256 * 1024**2, 1024**3, 1)
        limits = DynamicLimits()
        self.assertEqual(limits.resolve("workers", tiny), 1)
        self.assertLessEqual(limits.resolve("total_attachment_bytes", tiny), tiny.available_disk_bytes // 4)
