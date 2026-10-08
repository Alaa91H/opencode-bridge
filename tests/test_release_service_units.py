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
            self.assertIn("WorkingDirectory=/home/ubuntu/opencode-releases/current", unit)
            self.assertIn("EnvironmentFile=-/home/ubuntu/opencode-bridge/.env", unit)
        self.assertIn("Environment=OPENCODE_CONFIG=/home/ubuntu/opencode-releases/current/opencode.json", opencode)

    def test_deployed_tag_is_derived_from_release_version_without_git_metadata(self) -> None:
        with patch.object(systemd, "read_version", return_value="2.0.0-rc.9"):
            self.assertEqual(systemd.deployed_tag(), "v2.0.0-rc.9")


if __name__ == "__main__":
    unittest.main()
