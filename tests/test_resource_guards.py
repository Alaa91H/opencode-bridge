"""Resource guard rails, the opt-in reboot privilege and the unprivileged deployment fix."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVE = ROOT / "deploy" / "opencode-serve.service"
TELEGRAM = ROOT / "deploy" / "opencode-bridge-telegram.service"
POWER = ROOT / "maintenance" / "opencode-bridge-power.sudoers"
ADMIN = ROOT / "maintenance" / "opencode-bridge-admin.sudoers"
INSTALLER = ROOT / "maintenance" / "install-root-assets.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"


def directives(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith(("#", ";")) and not line.startswith("["):
            key, value = line.split("=", 1)
            found.setdefault(key.strip(), value.strip())
    return found


class UnitResourceTests(unittest.TestCase):
    def test_opencode_is_bounded_and_is_the_preferred_oom_victim(self):
        unit = directives(SERVE)
        self.assertRegex(unit["MemoryHigh"], r"^[1-9][0-9]?%$")
        self.assertGreater(int(unit["OOMScoreAdjust"]), 0,
                           "lowering the score needs root, so only raising is permitted")
        self.assertLess(int(unit["CPUWeight"]), 100)
        self.assertGreater(int(unit["Nice"]), 0)
        self.assertGreater(int(unit["TasksMax"]), 0)

    def test_no_hard_memory_kill_limit_that_could_abort_legitimate_work(self):
        for path in (SERVE, TELEGRAM):
            self.assertNotIn("MemoryMax", directives(path))
            self.assertNotIn("MemorySwapMax", directives(path))

    def test_the_bridge_is_never_made_a_more_attractive_oom_victim(self):
        self.assertNotIn("OOMScoreAdjust", directives(TELEGRAM))
        self.assertLess(int(directives(TELEGRAM)["TasksMax"]), int(directives(SERVE)["TasksMax"]))

    def test_crash_loops_back_off_instead_of_restarting_at_a_fixed_pace(self):
        for path in (SERVE, TELEGRAM):
            unit = directives(path)
            self.assertEqual(unit["Restart"], "on-failure")
            self.assertGreaterEqual(int(unit["RestartSteps"]), 3)
            self.assertGreaterEqual(int(unit["RestartMaxDelaySec"]), 30)

    def test_existing_hardening_is_preserved(self):
        for path in (SERVE, TELEGRAM):
            self.assertEqual(directives(path)["NoNewPrivileges"], "yes")

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is not installed")
    def test_systemd_accepts_every_directive(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in (SERVE, TELEGRAM):
                shutil.copy(path, tmp)
            result = subprocess.run(
                ["systemd-analyze", "verify", str(Path(tmp) / SERVE.name), str(Path(tmp) / TELEGRAM.name)],
                capture_output=True, text=True, timeout=30, check=False)
        problems = [line for line in (result.stdout + result.stderr).splitlines()
                    if re.search(r"Unknown (key|section)|Invalid|Failed to parse|ignoring", line)]
        self.assertEqual(problems, [])


class PowerSudoersTests(unittest.TestCase):
    def rules(self, path: Path) -> list[str]:
        return [line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    def test_grants_exactly_one_fixed_command_without_wildcards(self):
        rules = self.rules(POWER)
        self.assertEqual(rules, ["ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl reboot"])
        self.assertNotIn("*", rules[0])
        self.assertNotIn("ALL, ", rules[0])

    def test_uses_the_same_account_as_the_existing_admin_rule(self):
        self.assertEqual(self.rules(POWER)[0].split()[0], self.rules(ADMIN)[0].split()[0])

    def test_is_not_part_of_the_default_installation(self):
        text = INSTALLER.read_text(encoding="utf-8")
        default_install = text.split("readonly POWER_SUDOERS")[0]
        self.assertNotIn("opencode-bridge-power", default_install)

    def test_source_is_validated_before_it_can_reach_sudoers_d(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertLess(text.index('visudo -cf "${MAINTENANCE_DIR}/opencode-bridge-power.sudoers"'),
                        text.index('"$POWER_SUDOERS"\n    echo "power_control=enabled"'))

    def run_case(self, env_value: str | None) -> list[str]:
        text = INSTALLER.read_text(encoding="utf-8")
        block = text[text.index('case "${OPENCODE_ENABLE_POWER_CONTROL:-}" in'):]
        block = block[: block.index("esac") + 4]
        prelude = textwrap.dedent("""
            MAINTENANCE_DIR=/m; POWER_SUDOERS=/etc/sudoers.d/opencode-bridge-power
            visudo() { echo "visudo $*"; }
            install() { echo "install $*"; }
            rm() { echo "rm $*"; }
        """)
        export = "" if env_value is None else f"export OPENCODE_ENABLE_POWER_CONTROL={env_value}\n"
        result = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + prelude + export + block],
                                capture_output=True, text=True, timeout=10, check=True)
        return result.stdout.splitlines()

    def test_one_installs_zero_removes_and_unset_never_changes_the_choice(self):
        enabled = self.run_case("1")
        self.assertEqual(enabled[0], "visudo -cf /m/opencode-bridge-power.sudoers")
        self.assertTrue(enabled[1].startswith("install -m 0440 -o root -g root /m/opencode-bridge-power.sudoers"))
        self.assertEqual(enabled[-1], "power_control=enabled")
        self.assertEqual(self.run_case("0"),
                         ["rm -f /etc/sudoers.d/opencode-bridge-power", "power_control=disabled"])
        # The daily maintenance run calls the installer without the variable.
        self.assertEqual(self.run_case(None), [])
        self.assertEqual(self.run_case("yes"), [])


class UnprivilegedDeployTests(unittest.TestCase):
    """deploy.sh used to run `sudo -n install-root-assets.sh` under `set -e`.

    The shipped sudoers (correctly) does not allow that, so every deployment that ran as the
    service user failed and rolled back. The function must now degrade, not abort.
    """

    def extract(self) -> str:
        text = DEPLOY.read_text(encoding="utf-8")
        start = text.index("install_root_assets_if_permitted() {")
        end = text.index("\n}\n", start) + 3
        return text[start:end]

    def run_function(self, *, sudo_allows: bool, maintenance_changed: bool) -> tuple[int, str, bool]:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            flag = state / "root-assets-stale"
            if sudo_allows:
                flag.write_text("old\n")
            script = textwrap.dedent(f"""
                set -Eeuo pipefail
                BRIDGE_DIR=/b; DEPLOY_STATE_DIR={state}; DEPLOYED_COMMIT=aaa; TARGET_COMMIT=bbb
                sudo() {{
                  if [[ "$1 $2" == "-n -l" ]]; then return {0 if sudo_allows else 1}; fi
                  echo "sudo-ran $*"
                }}
                git() {{ return {1 if maintenance_changed else 0}; }}
            """) + self.extract() + "\ninstall_root_assets_if_permitted\necho finished\n"
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                    timeout=10, check=False)
            return result.returncode, result.stdout + result.stderr, flag.exists()

    def test_without_a_sudoers_rule_the_deployment_continues_and_flags_stale_assets(self):
        code, out, flagged = self.run_function(sudo_allows=False, maintenance_changed=True)
        self.assertEqual(code, 0, out)
        self.assertIn("finished", out)
        self.assertNotIn("sudo-ran", out)
        self.assertTrue(flagged)

    def test_no_flag_when_root_assets_did_not_change(self):
        code, out, flagged = self.run_function(sudo_allows=False, maintenance_changed=False)
        self.assertEqual(code, 0, out)
        self.assertFalse(flagged)

    def test_an_explicit_sudoers_permission_is_still_honoured_and_clears_the_flag(self):
        code, out, flagged = self.run_function(sudo_allows=True, maintenance_changed=True)
        self.assertEqual(code, 0, out)
        self.assertIn("sudo-ran -n /b/maintenance/install-root-assets.sh", out)
        self.assertFalse(flagged)

    def test_the_unconditional_sudo_call_is_gone(self):
        text = DEPLOY.read_text(encoding="utf-8")
        calls = [line for line in text.splitlines()
                 if "install-root-assets.sh" in line and "sudo -n" in line and not line.lstrip().startswith("#")]
        # Only the guarded call and the best-effort rollback remain; neither is a bare fatal call.
        for line in calls:
            self.assertTrue(line.lstrip().startswith(('if sudo -n -l', 'sudo -n "$installer"'))
                            or "|| true" in line, line)


if __name__ == "__main__":
    unittest.main()
