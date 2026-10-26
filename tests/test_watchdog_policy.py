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
            UpdateHealth(deployed_matches_head=True, behind_latest_release=0),
        )
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.score, 100)
        self.assertFalse(result.recoverable_services)

    def test_being_behind_a_published_release_degrades_health(self) -> None:
        result = assess_health(
            self.resources(),
            self.healthy_services(),
            QueueHealth(),
            UpdateHealth(deployed_matches_head=True, behind_latest_release=2),
        )
        self.assertEqual(result.status, "degraded")
        self.assertTrue(
            any("behind the latest release by 2 commit(s)" in issue for issue in result.issues),
        )

    def test_inactive_services_are_recoverable_and_critical(self) -> None:
        result = assess_health(
            self.resources(),
            ServiceHealth(False, False, False),
            QueueHealth(),
            UpdateHealth(),
        )
        self.assertIn(OPENCODE_SERVICE, result.recoverable_services)
        self.assertIn(TELEGRAM_SERVICE, result.recoverable_services)
        self.assertEqual(result.status, "critical")
        self.assertTrue(result.manual_action_required)

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


class MemoryIsNotPressureByItselfTests(unittest.TestCase):
    """Free memory alone must not be reported as pressure.

    Found live on the deployment host: 951 MiB of RAM, 255 MiB available, 5082
    MiB of swap with 4778 MiB free and PSI at zero, and the watchdog called it
    "degraded / high memory pressure" every five minutes. An alarm that fires
    when nothing is wrong is how the real alarm stops being read.
    """

    LIVE_HOST = {
        "total": 951, "available": 255, "swap_total": 5082,
        "swap_free": 4778, "psi": 0.0,
    }

    def assess(self, **overrides):
        values = {**self.LIVE_HOST, **overrides}
        return assess_health(
            ResourceSnapshot(
                values["total"], values["available"], values["swap_total"],
                values["swap_free"], 2, 0.3, 40.0, values["psi"],
            ),
            ServiceHealth(True, True, True),
            QueueHealth(),
            UpdateHealth(deployed_matches_head=True, behind_latest_release=0),
        )

    def test_the_shape_that_raised_a_false_alarm_is_healthy(self) -> None:
        result = self.assess()
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.score, 100)
        self.assertEqual(result.issues, ())

    def test_a_full_swap_restores_the_alarm(self) -> None:
        result = self.assess(swap_free=64)
        self.assertIn("high memory pressure", result.issues)
        self.assertEqual(result.status, "degraded")

    def test_unknown_swap_is_not_room(self) -> None:
        result = self.assess(swap_total=0, swap_free=0)
        self.assertIn("high memory pressure", result.issues)

    def test_no_headroom_and_critically_low_memory_asks_for_a_human(self) -> None:
        result = self.assess(available=90, swap_free=32)
        self.assertIn("critically low available memory", result.issues)
        self.assertTrue(result.manual_action_required)

    def test_stalls_are_reported_once_not_twice(self) -> None:
        result = self.assess(psi=3.0)
        self.assertIn("memory stalls detected", result.issues)
        self.assertNotIn("high memory pressure", result.issues)

    def test_stalls_above_ten_always_ask_for_a_human(self) -> None:
        result = self.assess(psi=12.0)
        self.assertIn("sustained memory stalls", result.issues)
        self.assertTrue(result.manual_action_required)


if __name__ == "__main__":
    unittest.main()
