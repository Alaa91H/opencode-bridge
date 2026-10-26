from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bridge_systemd", ROOT / "systemd.py")
assert SPEC is not None and SPEC.loader is not None
systemd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(systemd)


class ReleaseServiceUnitTests(unittest.TestCase):
    def test_units_run_from_atomic_current_path_and_keep_shared_environment(self) -> None:
        telegram = (ROOT / "deploy" / "opencode-bridge-telegram.service").read_text(encoding="utf-8")
        opencode = (ROOT / "deploy" / "opencode-serve.service").read_text(encoding="utf-8")
        for unit in (telegram, opencode):
            self.assertIn("WorkingDirectory=/home/ubuntu/opencode-bridge", unit)
            self.assertIn("EnvironmentFile=-/home/ubuntu/opencode-bridge/.env", unit)
        self.assertIn("Environment=OPENCODE_CONFIG=/home/ubuntu/opencode-bridge/opencode.json", opencode)
        self.assertIn("Environment=OPENCODE_CONFIG_DIR=/home/ubuntu/opencode-bridge", opencode)

    def test_stack_target_weakly_wants_members_so_restarts_stay_isolated(self) -> None:
        """A member restart must not stop the target and, through PartOf=, its sibling.

        Found on the deployed host: ``Requires=`` on the target combined with
        ``Restart=on-failure`` on a member made systemd stop the target while the
        failed member restarted. The sibling then stopped too, so killing the
        Telegram process aborted a running OpenCode task.
        """
        target = (ROOT / "deploy" / "opencode-bridge.target").read_text(encoding="utf-8")
        directives = {
            line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in target.splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        self.assertEqual(
            directives.get("Wants"),
            "opencode-serve.service opencode-bridge-telegram.service",
        )
        self.assertNotIn("Requires", directives)
        for name in ("opencode-serve.service", "opencode-bridge-telegram.service"):
            unit = (ROOT / "deploy" / name).read_text(encoding="utf-8")
            self.assertIn("PartOf=opencode-bridge.target", unit)

    def test_deployed_tag_is_derived_from_release_version_without_git_metadata(self) -> None:
        with patch.object(systemd, "read_version", return_value="2.0.0-rc.9"):
            self.assertEqual(systemd.deployed_tag(), "v2.0.0-rc.9")


if __name__ == "__main__":
    unittest.main()
