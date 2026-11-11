from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
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

    def test_install_probes_and_writes_only_the_host_specific_fallback(self) -> None:
        bad = {"ProtectClock", "ProtectKernelLogs", "ProtectKernelModules"}

        def probe(properties: tuple[str, ...]) -> bool:
            return not (set(properties) & bad)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(systemd, "_run"):
                systemd.install(True, home=home, hardening_probe=probe)
            dropin = (
                home
                / ".config"
                / "systemd"
                / "user"
                / "opencode-bridge-telegram.service.d"
                / "20-hardening-compat.conf"
            )
            text = dropin.read_text(encoding="utf-8")
            for name in bad:
                self.assertIn(f"{name}=no", text)
            self.assertNotIn("ProtectKernelTunables=no", text)
            self.assertNotIn("NoNewPrivileges=no", text)

    def test_install_keeps_full_hardening_when_the_probe_accepts_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(systemd, "_run"):
                systemd.install(True, home=home, hardening_probe=lambda _properties: True)
            dropin = (
                home
                / ".config"
                / "systemd"
                / "user"
                / "opencode-bridge-telegram.service.d"
                / "20-hardening-compat.conf"
            )
            self.assertFalse(dropin.exists())

    def test_self_check_probes_without_installing_or_writing(self) -> None:
        output = io.StringIO()
        with (
            patch.object(systemd, "supported_properties", return_value=("ProtectHostname",)),
            patch.object(systemd, "install") as install,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(systemd.main(["--self-check"]), 0)
        install.assert_not_called()
        self.assertIn('"supported"', output.getvalue())
        self.assertIn("ProtectHostname", output.getvalue())


if __name__ == "__main__":
    unittest.main()
