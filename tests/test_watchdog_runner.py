from __future__ import annotations

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

import watchdog_runner
from resource_monitor import ResourceSnapshot
from watchdog_policy import (
    OPENCODE_SERVICE,
    QueueHealth,
    ServiceHealth,
    UpdateHealth,
)


class WatchdogRestartTests(unittest.TestCase):
    def test_restart_timeout_covers_the_unit_stop_grace_period(self) -> None:
        completed = subprocess.CompletedProcess(["systemctl"], 0, "", "")
        with patch.object(watchdog_runner, "_run", return_value=completed) as run:
            self.assertTrue(watchdog_runner._restart_service(OPENCODE_SERVICE))

        self.assertGreaterEqual(
            run.call_args.kwargs["timeout"],
            150.0,
            "opencode-serve has TimeoutStopSec=120; a shorter timeout reports normal stops as failures",
        )

    def test_failed_restart_is_recorded_so_cooldown_and_loop_guard_apply(self) -> None:
        resources = ResourceSnapshot(4096, 3072, 1024, 1024, 4, 0.5, 50.0, 0.0)
        services = ServiceHealth(False, False, True)
        report = {
            "status": "critical",
            "score": 10,
            "issues": ["OpenCode service is inactive"],
            "manual_action_required": True,
            "actions": [],
        }
        saved: list[dict] = []
        with (
            patch.object(
                watchdog_runner, "_collect",
                return_value=(resources, services, QueueHealth(), UpdateHealth()),
            ),
            patch.object(watchdog_runner, "_restart_service", return_value=False) as restart,
            patch.object(watchdog_runner, "_write_report", return_value=report),
            patch.object(watchdog_runner, "_save_state", side_effect=saved.append),
            patch.object(watchdog_runner, "AuditLogger"),
            patch.object(watchdog_runner.time, "time", return_value=1000.0),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                watchdog_runner.main()

        restart.assert_called_once_with(OPENCODE_SERVICE)
        self.assertEqual(saved[0]["restart_history"][OPENCODE_SERVICE], [1000.0])
        self.assertGreaterEqual(len(saved), 2, "attempt must be persisted before restart")


if __name__ == "__main__":
    unittest.main()
