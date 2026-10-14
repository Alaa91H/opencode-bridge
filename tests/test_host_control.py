"""Host control: fixed argv, bounded output, redaction, detached units, /proc parsing."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge.services import host_control
from bridge.services.cache_retention import manual_cleanup, midnight_cleanup
from bridge.services.host_control import (
    ActionResult,
    CommandResult,
    DetachedUnavailable,
    HostControl,
    HostControlError,
    launch_transient,
    redact,
    run_fixed,
)


class RedactionTests(unittest.TestCase):
    def test_masks_common_credentials_but_keeps_context(self):
        token = "123456789:" + "A" * 35
        text = (f"bot {token} ok\nAuthorization: Bearer abcdefghijklmnop1234\n"
                "url https://user:hunter2pass@example.com/x\n"
                "OPENCODE_PASSWORD=supersecret\nkey sk-abcdefghijklmnopqrstuv\n"
                "ghp_" + "b" * 30)
        cleaned = redact(text)
        for secret in (token, "abcdefghijklmnop1234", "hunter2pass", "supersecret",
                       "sk-abcdefghijklmnopqrstuv", "b" * 30):
            self.assertNotIn(secret, cleaned)
        self.assertIn("example.com", cleaned)
        self.assertIn("ok", cleaned)

    def test_plain_log_lines_are_unchanged(self):
        line = "2026-10-14T03:00:00+0000 host unit[1]: Started service in 3 seconds"
        self.assertEqual(redact(line), line)


class RunFixedTests(unittest.IsolatedAsyncioTestCase):
    async def test_arguments_are_never_interpreted_by_a_shell(self):
        result = await run_fixed(["/bin/echo", "a;b && $(whoami) `id`"], timeout=5)
        self.assertEqual(result, CommandResult(0, "a;b && $(whoami) `id`\n"))

    async def test_output_is_bounded_and_process_killed(self):
        result = await run_fixed(
            [sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x'*4096)"],
            timeout=10, max_bytes=20_000)
        self.assertLessEqual(len(result.output), 20_000)

    async def test_timeout_raises_and_kills(self):
        with self.assertRaises(HostControlError):
            await run_fixed([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3)

    async def test_rejects_malformed_commands_and_missing_binaries(self):
        with self.assertRaises(HostControlError):
            await run_fixed([], timeout=1)
        with self.assertRaises(HostControlError):
            await run_fixed(["echo", "bad\x00arg"], timeout=1)
        with self.assertRaises(HostControlError):
            await run_fixed(["/nonexistent/binary-xyz"], timeout=1)

    async def test_control_sequences_are_stripped(self):
        result = await run_fixed(["/usr/bin/printf", "\\033[31mred\\033[0m ok\\007"], timeout=5)
        self.assertEqual(result.output, "red ok")


class LaunchTransientTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_a_separate_cgroup_unit_with_allow_listed_environment(self):
        captured = []

        async def fake_run(argv, *, timeout, **_):
            captured.append(list(argv))
            return CommandResult(0, "")

        with patch.object(host_control.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(host_control, "run_fixed", fake_run), \
                patch.object(host_control.time, "time", return_value=1700000000):
            unit = await launch_transient(
                ["/usr/bin/python3", "self_update.py", "--apply"], name="opencode-bridge-update",
                delay_seconds=3, working_directory=Path("/srv/app"),
                environment={"OPENCODE_AUTO_UPDATE": "1"})
        self.assertEqual(unit, "opencode-bridge-update-1700000000")
        argv = captured[0]
        self.assertEqual(argv[0], "/usr/bin/systemd-run")
        for flag in ("--user", "--collect", "--unit=opencode-bridge-update-1700000000",
                     "--on-active=3s", "--working-directory=/srv/app",
                     "--setenv=OPENCODE_AUTO_UPDATE=1"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--") + 1:],
                         ["/usr/bin/python3", "self_update.py", "--apply"])

    async def test_fails_closed_without_systemd_run(self):
        with patch.object(host_control.shutil, "which", return_value=None):
            with self.assertRaises(DetachedUnavailable):
                await launch_transient(["true"], name="x")

    async def test_fails_closed_when_systemd_refuses(self):
        with patch.object(host_control.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(host_control, "run_fixed", AsyncMock(return_value=CommandResult(1, "no bus"))):
            with self.assertRaises(DetachedUnavailable):
                await launch_transient(["true"], name="x")

    async def test_rejects_unsafe_names_and_environment(self):
        for name in ("a b", "a/b", "", "x;y", "x" * 80):
            with self.assertRaises(ValueError):
                await launch_transient(["true"], name=name)
        with patch.object(host_control.shutil, "which", return_value="/usr/bin/systemd-run"):
            for env in ({"lower": "1"}, {"OK": "a\nb"}, {"OK": "a\x00b"}):
                with self.assertRaises(ValueError):
                    await launch_transient(["true"], name="ok", environment=env)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.control = HostControl(Path(self.tmp.name), proc_root=Path(self.tmp.name) / "proc",
                                   backup_root=Path(self.tmp.name) / "backups")

    async def test_unknown_keys_are_rejected_before_any_command(self):
        with patch.object(host_control, "run_fixed", AsyncMock()) as run:
            with self.assertRaises(ValueError):
                await self.control.restart_service("../../etc/passwd")
            with self.assertRaises(ValueError):
                await self.control.service_logs("sshd")
        run.assert_not_called()

    async def test_restarting_the_bridge_is_delegated_to_a_detached_unit(self):
        with patch.object(host_control, "launch_transient", AsyncMock(return_value="u")) as launch, \
                patch.object(host_control, "run_fixed", AsyncMock()) as run:
            for key, unit in (("bridge", "opencode-bridge-telegram.service"),
                              ("stack", "opencode-bridge.target")):
                result = await self.control.restart_service(key)
                self.assertEqual(result, ActionResult(True, "scheduled"))
                argv = launch.await_args.args[0]
                self.assertEqual(argv[-3:], ["--user", "restart", unit][-3:])
                self.assertEqual(launch.await_args.kwargs["delay_seconds"], 3)
        run.assert_not_called()  # never restarted in-process: it would kill this bot

    async def test_detached_restart_reports_unavailable_without_changing_anything(self):
        with patch.object(host_control, "launch_transient",
                          AsyncMock(side_effect=DetachedUnavailable("no systemd-run"))):
            result = await self.control.restart_service("bridge")
        self.assertEqual((result.ok, result.code), (False, "unavailable"))

    async def test_opencode_restart_verifies_the_unit_is_active(self):
        calls = []

        async def fake_run(argv, *, timeout, **_):
            calls.append(list(argv))
            return CommandResult(0, "active\n" if "is-active" in argv else "")

        with patch.object(host_control, "run_fixed", fake_run):
            result = await self.control.restart_service("opencode")
        self.assertEqual(result, ActionResult(True, "restarted"))
        self.assertIn("restart", calls[0])
        self.assertIn("opencode-serve.service", calls[0])
        self.assertIn("is-active", calls[1])

    async def test_failed_restart_is_reported_and_redacted(self):
        with patch.object(host_control, "run_fixed",
                          AsyncMock(return_value=CommandResult(1, "failed token=abc123secretvalue"))):
            result = await self.control.restart_service("opencode")
        self.assertEqual((result.ok, result.code), (False, "failed"))
        self.assertNotIn("abc123secretvalue", result.detail)

    async def test_logs_are_redacted_trimmed_and_use_fixed_argv(self):
        captured = []
        noise = "line\n" * 2000 + "password=hunter2hunter2\n"

        async def fake_run(argv, *, timeout, **_):
            captured.append(list(argv))
            return CommandResult(0, noise)

        with patch.object(host_control, "run_fixed", fake_run):
            text = await self.control.service_logs("opencode", lines=500)
        self.assertLessEqual(len(text), 3000)
        self.assertNotIn("hunter2hunter2", text)
        self.assertEqual(captured[0][:5], ["journalctl", "--user", "-u", "opencode-serve.service", "-n"])
        self.assertEqual(captured[0][5], "80")  # clamped

    async def test_unreadable_journal_is_a_clear_error(self):
        with patch.object(host_control, "run_fixed", AsyncMock(return_value=CommandResult(1, ""))):
            with self.assertRaises(HostControlError):
                await self.control.service_logs("bridge")

    async def test_failed_units_are_parsed_and_scoped(self):
        async def fake_run(argv, *, timeout, **_):
            if "--user" in argv:
                return CommandResult(0, "foo.service loaded failed failed Foo\n")
            return CommandResult(0, "bar.service loaded failed failed Bar\n$(evil) x\n")

        with patch.object(host_control, "run_fixed", fake_run):
            names = await self.control.failed_units()
        self.assertEqual(names, ["user:foo.service", "system:bar.service"])


def _write_proc(root: Path, pid: int, comm: str, utime: int, stime: int, rss_pages: int) -> None:
    fields = ["S", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0", str(utime), str(stime),
              "0", "0", "20", "0", "1", "0", "100", "1000", str(rss_pages)]
    entry = root / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + " 0 0\n")


class ConsumptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        self.control = HostControl(self.root, proc_root=self.proc, backup_root=self.root / "backups")

    async def test_processes_sorted_by_memory_with_cpu_from_two_samples(self):
        page = os.sysconf("SC_PAGE_SIZE")
        ticks = os.sysconf("SC_CLK_TCK")
        _write_proc(self.proc, 10, "small", 0, 0, 100)
        _write_proc(self.proc, 20, "big (x) y", 0, 0, 50_000)
        (self.proc / "self").mkdir()
        (self.proc / "notapid").mkdir()

        async def bump(_seconds):
            _write_proc(self.proc, 20, "big (x) y", ticks // 2, 0, 50_000)

        with patch.object(host_control.asyncio, "sleep", bump):
            rows = await self.control.top_processes(limit=5, interval=1.0)
        self.assertEqual([row["pid"] for row in rows], [20, 10])
        self.assertEqual(rows[0]["name"], "big (x) y")
        self.assertEqual(rows[0]["rss"], 50_000 * page)
        self.assertAlmostEqual(rows[0]["cpu"], 50.0, delta=1.0)
        self.assertEqual(rows[1]["cpu"], 0.0)

    async def test_corrupt_proc_entries_are_skipped(self):
        (self.proc / "5").mkdir(parents=True)
        (self.proc / "5" / "stat").write_text("garbage")
        _write_proc(self.proc, 6, "ok", 1, 1, 10)
        with patch.object(host_control.asyncio, "sleep", AsyncMock()):
            rows = await self.control.top_processes(interval=0.1)
        self.assertEqual([row["pid"] for row in rows], [6])

    async def test_disk_breakdown_sorts_ignores_symlinks_and_reports_free_space(self):
        (self.root / "runtime" / "downloads").mkdir(parents=True)
        (self.root / "runtime" / "downloads" / "a.bin").write_bytes(b"x" * 5000)
        (self.root / "runtime" / "small.txt").write_bytes(b"y" * 10)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "huge").write_bytes(b"z" * 100_000)
        (self.root / "runtime" / "link").symlink_to(outside)
        (self.root / "sessions.db").write_bytes(b"d" * 20_000)
        data = await self.control.disk_breakdown()
        labels = [label for label, _size, _t in data["items"]]
        self.assertEqual(labels[0], "sessions.db")
        self.assertIn("runtime/downloads", labels)
        self.assertNotIn("runtime/link", labels)
        self.assertGreater(data["free"], 0)

    async def test_disk_breakdown_flags_partial_measurement(self):
        big = self.root / "runtime" / "many"
        big.mkdir(parents=True)
        for index in range(30):
            (big / f"f{index}").write_bytes(b"1")
        data = await self.control.disk_breakdown(entry_budget=10)
        row = next(item for item in data["items"] if item[0] == "runtime/many")
        self.assertTrue(row[2])


class PowerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.control = HostControl(Path(self.tmp.name))

    async def test_reboot_is_refused_unless_sudoers_entry_exists(self):
        with patch.object(host_control, "run_fixed", AsyncMock(return_value=CommandResult(1, ""))), \
                patch.object(host_control, "launch_transient", AsyncMock()) as launch:
            result = await self.control.reboot()
        self.assertEqual((result.ok, result.code), (False, "power_disabled"))
        launch.assert_not_called()

    async def test_reboot_is_scheduled_through_a_delayed_detached_unit(self):
        with patch.object(host_control, "run_fixed", AsyncMock(return_value=CommandResult(0, ""))), \
                patch.object(host_control, "launch_transient", AsyncMock(return_value="u")) as launch:
            result = await self.control.reboot()
        self.assertEqual(result, ActionResult(True, "scheduled"))
        self.assertEqual(launch.await_args.args[0],
                         ["sudo", "-n", "/usr/bin/systemctl", "reboot"])
        self.assertEqual(launch.await_args.kwargs["delay_seconds"], 5)

    async def test_missing_sudo_binary_means_disabled(self):
        with patch.object(host_control, "run_fixed", AsyncMock(side_effect=HostControlError("x"))):
            self.assertFalse(await self.control.power_available())


class ManualCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / "runtime" / "downloads").mkdir(parents=True)
        self.database = self.project / "sessions.db"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE agent_tasks (id INTEGER PRIMARY KEY, status TEXT)")

    def test_manual_cleanup_is_not_limited_to_the_midnight_window(self):
        noon = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
        self.assertEqual(midnight_cleanup(self.project, noon)["status"], "skipped")
        self.assertEqual(manual_cleanup(self.project, noon)["status"], "completed")

    def test_manual_cleanup_never_runs_while_tasks_are_unfinished(self):
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO agent_tasks(status) VALUES ('running')")
        result = manual_cleanup(self.project, datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
        self.assertEqual(result, {"status": "skipped", "reason": "unfinished agent tasks"})

    def test_manual_cleanup_skips_during_a_transfer(self):
        (self.project / "runtime" / "downloads" / "movie.part").write_bytes(b"1")
        result = manual_cleanup(self.project, datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
        self.assertEqual(result["reason"], "download transfer in progress")

    def test_manual_cleanup_still_honours_emergency_stop(self):
        (self.project / "runtime" / "autopilot-mode").write_text("emergency-stop\n")
        result = manual_cleanup(self.project, datetime.now(UTC) + timedelta(hours=1))
        self.assertEqual(result["status"], "skipped")
        self.assertIn("emergency", result["reason"])

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            manual_cleanup(self.project, datetime(2026, 10, 14, 12, 0))

    def test_refuses_a_symlinked_cache(self):
        (self.project / "runtime" / "downloads").rmdir()
        (self.project / "runtime" / "downloads").symlink_to(self.project)
        with self.assertRaises(ValueError):
            manual_cleanup(self.project, datetime(2026, 10, 14, 12, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
