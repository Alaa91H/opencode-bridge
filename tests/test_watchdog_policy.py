from __future__ import annotations

import unittest

from resource_monitor import ResourceSnapshot
from watchdog_policy import (
    OPENCODE_SERVICE,
    TELEGRAM_SERVICE,
    QueueHealth,
    ServiceHealth,
    UpdateHealth,
    assess_health,
    restart_allowed,
)


class WatchdogPolicyTests(unittest.TestCase):
    def resources(self, available=3072, total=4096, disk=50.0, load=0.5, cpus=4, psi=0.0):
        return ResourceSnapshot(total, available, 1024, 1024, cpus, load, disk, psi)

    def healthy_services(self):
        return ServiceHealth(True, True, True)

    def test_healthy_host_scores_100(self) -> None:
        result = assess_health(
            self.resources(),
            self.healthy_services(),
            QueueHealth(),
            UpdateHealth(deployed_matches_head=True, behind_origin_main=0),
        )
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.score, 100)
        self.assertFalse(result.recoverable_services)

    def test_inactive_services_are_recoverable(self) -> None:
        result = assess_health(
            self.resources(),
            ServiceHealth(False, False, False),
            QueueHealth(),
            UpdateHealth(),
        )
        self.assertIn(OPENCODE_SERVICE, result.recoverable_services)
        self.assertIn(TELEGRAM_SERVICE, result.recoverable_services)
        self.assertEqual(result.status, "degraded")

    def test_stale_task_requires_manual_review(self) -> None:
        result = assess_health(
            self.resources(),
            self.healthy_services(),
            QueueHealth(running=1, stale_running=1),
            UpdateHealth(),
        )
        self.assertTrue(result.manual_action_required)
        self.assertTrue(any("stale threshold" in issue for issue in result.issues))

    def test_critical_resources_block_restart(self) -> None:
        history: dict[str, list[float]] = {}
        allowed, reason = restart_allowed(
            OPENCODE_SERVICE,
            self.resources(available=64),
            history,
            now=1000.0,
        )
        self.assertFalse(allowed)
        self.assertIn("constrained", reason)

    def test_unknown_service_is_never_restarted(self) -> None:
        allowed, _ = restart_allowed("ssh.service", self.resources(), {}, now=1000.0)
        self.assertFalse(allowed)

    def test_restart_cooldown_blocks_repeat(self) -> None:
        history = {OPENCODE_SERVICE: [900.0]}
        allowed, reason = restart_allowed(
            OPENCODE_SERVICE,
            self.resources(),
            history,
            now=1000.0,
            cooldown_seconds=300,
        )
        self.assertFalse(allowed)
        self.assertIn("cooldown", reason)

    def test_hourly_restart_limit_blocks_loop(self) -> None:
        history = {OPENCODE_SERVICE: [100.0, 500.0, 900.0]}
        allowed, reason = restart_allowed(
            OPENCODE_SERVICE,
            self.resources(),
            history,
            now=1200.0,
            cooldown_seconds=60,
            max_restarts_per_hour=3,
        )
        self.assertFalse(allowed)
        self.assertIn("hourly limit", reason)


if __name__ == "__main__":
    unittest.main()
