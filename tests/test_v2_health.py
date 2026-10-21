import unittest

from bridge.services.health_service import (
    ComponentHealth,
    HealthService,
    HealthState,
    systemd_readiness_message,
)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.service = HealthService()
        self.healthy = {
            name: ComponentHealth(name, HealthState.HEALTHY)
            for name in self.service.COMPONENTS
        }

    def test_liveness_is_independent_of_dependencies(self):
        self.assertTrue(self.service.liveness())

    def test_all_required_components_are_reported(self):
        report = self.service.health(self.healthy)
        self.assertEqual(report.state, HealthState.HEALTHY)
        self.assertEqual({c.name for c in report.components}, set(self.service.COMPONENTS))
        self.assertTrue(self.service.readiness(self.healthy))

    def test_required_failure_blocks_readiness(self):
        checks = dict(self.healthy)
        checks["db"] = ComponentHealth("db", HealthState.UNHEALTHY, "locked")
        self.assertFalse(self.service.readiness(checks))
        self.assertEqual(self.service.health(checks).state, HealthState.UNHEALTHY)

    def test_optional_failure_is_degraded_but_ready(self):
        checks = dict(self.healthy)
        checks["model_catalog"] = ComponentHealth(
            "model_catalog", HealthState.UNHEALTHY, "stale", required=False)
        self.assertTrue(self.service.readiness(checks))
        self.assertEqual(self.service.health(checks).state, HealthState.DEGRADED)

    def test_systemd_readiness_protocol(self):
        self.assertIn("READY=1", systemd_readiness_message(True))
        self.assertIn("READY=0", systemd_readiness_message(False, "DB unavailable"))
