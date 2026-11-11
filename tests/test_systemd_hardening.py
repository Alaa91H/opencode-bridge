"""Tests for host-specific systemd hardening detection and fallback."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.deployment import systemd_hardening

BAD = {"ProtectClock", "ProtectKernelLogs", "ProtectKernelModules"}


def probe_with(bad: set[str]):
    def probe(properties: tuple[str, ...]) -> bool:
        return not (set(properties) & bad)

    return probe


class SystemdHardeningTests(unittest.TestCase):
    def test_probe_keeps_supported_directives_and_disables_only_rejected_ones(self) -> None:
        supported = systemd_hardening.supported_properties(probe_with(BAD))
        self.assertEqual(set(supported), set(systemd_hardening.HARDENING_PROPERTIES) - BAD)
        self.assertEqual(set(systemd_hardening.disabled_properties(supported)), BAD)

    def test_a_probe_that_cannot_run_disables_optional_hardening_conservatively(self) -> None:
        supported = systemd_hardening.supported_properties(lambda _properties: False)
        self.assertEqual(supported, ())
        disabled = systemd_hardening.disabled_properties(supported)
        self.assertEqual(set(disabled), set(systemd_hardening.HARDENING_PROPERTIES))

    def test_full_capability_host_removes_a_stale_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stale = directory / systemd_hardening.COMPAT_DROPIN_NAME
            stale.write_text("old\n", encoding="utf-8")

            disabled = systemd_hardening.install_compat_dropin(
                directory,
                supported=systemd_hardening.HARDENING_PROPERTIES,
            )

            self.assertEqual(disabled, ())
            self.assertFalse(stale.exists())

    def test_fallback_writes_only_the_rejected_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            disabled = systemd_hardening.install_compat_dropin(
                directory,
                supported=set(systemd_hardening.HARDENING_PROPERTIES) - BAD,
            )

            target = directory / systemd_hardening.COMPAT_DROPIN_NAME
            text = target.read_text(encoding="utf-8")
            self.assertEqual(set(disabled), BAD)
            for name in BAD:
                self.assertIn(f"{name}=no", text)
            for name in set(systemd_hardening.HARDENING_PROPERTIES) - BAD:
                self.assertNotIn(f"{name}=no", text)
            self.assertNotIn("NoNewPrivileges=no", text)
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_self_check_is_deterministic_and_injectable(self) -> None:
        result = systemd_hardening.self_check(probe_with(BAD))
        self.assertEqual(set(result["disabled"]), BAD)
        self.assertEqual(result["no_new_privileges"], "yes")
        self.assertEqual(result["compat_dropin"], systemd_hardening.COMPAT_DROPIN_NAME)


if __name__ == "__main__":
    unittest.main()
