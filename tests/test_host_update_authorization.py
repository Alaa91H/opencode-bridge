"""Contract tests for the root maintenance OS update authorization gate."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "maintenance" / "daily-maintenance.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "Bash required for host policy checks")
class HostUpdateAuthorizationTests(unittest.TestCase):
    def test_daily_root_maintenance_requires_literal_one(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        prefix = 'if [[ -f "${RUNTIME_DIR}/host-auto-update-enabled" ]] &&'
        start = source.index(prefix)
        end = source.index(" then\n", start) + len(" then\n")
        guard = source[start:end]

        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            switch = runtime / "host-auto-update-enabled"

            def allowed() -> bool:
                completed = subprocess.run(
                    [str(BASH), "-e", "-c", guard + "printf yes; else printf no; fi"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env={**os.environ, "RUNTIME_DIR": folder},
                    check=True,
                )
                return completed.stdout == "yes"

            self.assertFalse(allowed(), "Missing flag must not run apt as root")
            switch.write_text("1\n", encoding="ascii")
            self.assertTrue(allowed(), "Explicit true allows configured daily APT")
            switch.write_text("0\n", encoding="ascii")
            self.assertFalse(allowed())
            switch.write_text("something\n", encoding="ascii")
            self.assertFalse(allowed())
            switch.unlink()
            try:
                switch.symlink_to(runtime / "another-file")
            except (OSError, NotImplementedError):
                return
            (runtime / "another-file").write_text("1\n", encoding="ascii")
            self.assertFalse(allowed(), "Symlink cannot authorize host package updates")

    def test_autoremove_is_only_a_simulation(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("-y autoremove --purge", source)
        self.assertIn(" -s autoremove", source)
        self.assertIn("apt_autoremove_preview", source)


if __name__ == "__main__":
    unittest.main()
