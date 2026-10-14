"""A Bridge update restarts the service that requested it: prove it survives and reports."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bridge.services import server_admin, update_report
from bridge.services.host_control import DetachedUnavailable
from bridge.services.update_coordinator import UpdateCoordinator, write_quarantine

PROJECT_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "maintenance_self_update_detached", PROJECT_DIR / "maintenance" / "self_update.py")
self_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(self_update)

OLD = "a" * 40
NEW = "b" * 40


class LedgerReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.coordinator = UpdateCoordinator(self.runtime)

    def _interrupted(self, tag="v3.5.0", commit=NEW):
        self.coordinator.transition(tag, commit, "verified")
        self.coordinator.transition(tag, commit, "applying")

    def test_nothing_pending_means_nothing_to_do(self):
        self.assertIsNone(self.coordinator.reconcile_interrupted(NEW))
        self._interrupted()
        self.coordinator.transition("v3.5.0", NEW, "healthy")
        self.assertIsNone(self.coordinator.reconcile_interrupted(NEW))

    def test_confirmed_deployment_becomes_healthy_when_only_the_last_write_was_lost(self):
        self._interrupted()
        resolved = self.coordinator.reconcile_interrupted(NEW)
        self.assertEqual(resolved.phase, "healthy")
        self.assertEqual(self.coordinator.latest().phase, "healthy")

    def test_unconfirmed_deployment_is_quarantined_never_retried_blindly(self):
        self._interrupted()
        for deployed in (OLD, None, "not-a-sha", "B" * 40):
            self.setUp()
            self._interrupted()
            self.assertEqual(self.coordinator.reconcile_interrupted(deployed).phase, "quarantined")

    def test_an_interrupted_update_no_longer_blocks_the_next_release(self):
        self._interrupted()
        with self.assertRaises(ValueError):  # the pre-fix permanent deadlock
            self.coordinator.transition("v3.6.0", "c" * 40, "verified")
            self.coordinator.transition("v3.6.0", "c" * 40, "applying")
        self.coordinator.reconcile_interrupted(NEW)
        self.coordinator.transition("v3.6.0", "c" * 40, "applying")

    def test_quarantine_record_is_honoured_by_the_release_updater(self):
        write_quarantine(self.runtime, "v3.5.0", NEW, "interrupted")
        self.assertEqual((self.runtime / "auto-update-quarantine.json").stat().st_mode & 0o777, 0o600)
        # self_update expects <project>/runtime/..., so mirror that layout.
        project = Path(self.tmp.name) / "project"
        (project / "runtime").mkdir(parents=True)
        write_quarantine(project / "runtime", "v3.5.0", NEW, "interrupted")
        self.assertTrue(self_update.is_quarantined(project, "v3.5.0"))
        self.assertFalse(self_update.is_quarantined(project, "v3.6.0"))

    def test_quarantine_refuses_symlink_and_bad_values(self):
        (self.runtime / "auto-update-quarantine.json").symlink_to(self.runtime / "elsewhere")
        with self.assertRaises(ValueError):
            write_quarantine(self.runtime, "v3.5.0", NEW, "x")
        with self.assertRaises(ValueError):
            write_quarantine(self.runtime, "../evil", NEW, "x")
        with self.assertRaises(ValueError):
            write_quarantine(self.runtime, "v3.5.0", "short", "x")


class ResultFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)

    def test_round_trip_is_private_and_sanitised(self):
        update_report.write_result(self.runtime, status="error", trigger="apply", tag="v3.5.0",
                                   commit=NEW, reason="boom\n\x1b[31mred é")
        path = update_report.result_path(self.runtime)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        value = update_report.read_result(self.runtime)
        self.assertEqual(value["status"], "error")
        self.assertNotIn("\n", value["reason"])
        self.assertNotIn("\x1b", value["reason"])

    def test_invalid_status_and_tag_are_rejected_or_dropped(self):
        with self.assertRaises(ValueError):
            update_report.write_result(self.runtime, status="pwned", trigger="apply")
        update_report.write_result(self.runtime, status="updated", trigger="apply", tag="../x")
        self.assertIsNone(update_report.read_result(self.runtime)["tag"])

    def test_corrupt_old_future_and_symlinked_results_are_ignored(self):
        path = update_report.result_path(self.runtime)
        path.write_text("{not json")
        self.assertIsNone(update_report.read_result(self.runtime))
        path.write_text(json.dumps({"schema": 2, "status": "updated"}))
        self.assertIsNone(update_report.read_result(self.runtime))
        path.write_text(json.dumps({"schema": 1, "status": "updated"}))
        old = time.time() - 7 * 3600
        os.utime(path, (old, old))
        self.assertIsNone(update_report.read_result(self.runtime))
        self.assertIsNotNone(update_report.read_result(self.runtime, max_age=10 * 3600))
        os.utime(path)
        self.assertIsNone(update_report.read_result(self.runtime, since=time.time() + 60))
        path.unlink()
        path.symlink_to(self.runtime / "target")
        self.assertIsNone(update_report.read_result(self.runtime))
        with self.assertRaises(ValueError):
            update_report.write_result(self.runtime, status="updated", trigger="apply")

    def test_consume_moves_the_file_so_it_is_announced_once(self):
        update_report.write_result(self.runtime, status="updated", trigger="apply", tag="v3.5.0")
        update_report.consume_result(self.runtime)
        self.assertIsNone(update_report.read_result(self.runtime))
        self.assertTrue((self.runtime / update_report.REPORTED_FILE).exists())


class AnnounceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)

    async def test_sends_the_result_once_and_consumes_it(self):
        update_report.write_result(self.runtime, status="updated", trigger="apply", tag="v3.5.0")
        sent = []

        async def send(text):
            sent.append(text)

        first = await update_report.announce_update_outcome(send, self.runtime)
        second = await update_report.announce_update_outcome(send, self.runtime)
        self.assertIn("v3.5.0", first)
        self.assertIsNone(second)
        self.assertEqual(len(sent), 1)

    async def test_a_failed_send_keeps_the_result_for_the_next_start(self):
        update_report.write_result(self.runtime, status="error", trigger="apply", tag="v3.5.0",
                                   reason="health check failed")
        send = AsyncMock(side_effect=RuntimeError("telegram down"))
        self.assertIsNone(await update_report.announce_update_outcome(send, self.runtime))
        self.assertIsNotNone(update_report.read_result(self.runtime))

    async def test_silent_when_nothing_happened(self):
        send = AsyncMock()
        self.assertIsNone(await update_report.announce_update_outcome(send, self.runtime))
        send.assert_not_called()

    async def test_killed_updater_is_reconciled_and_reported_without_a_result_file(self):
        coordinator = UpdateCoordinator(self.runtime)
        coordinator.transition("v3.5.0", NEW, "verified")
        coordinator.transition("v3.5.0", NEW, "applying")
        (self.runtime / "deployed-ref").write_text(NEW + "\n")
        sent = []

        async def send(text):
            sent.append(text)

        await update_report.announce_update_outcome(send, self.runtime)
        self.assertEqual(coordinator.latest().phase, "healthy")
        self.assertIn("v3.5.0", sent[0])

    async def test_killed_before_confirmation_quarantines_and_blocks_retry(self):
        coordinator = UpdateCoordinator(self.runtime)
        coordinator.transition("v3.5.0", NEW, "verified")
        coordinator.transition("v3.5.0", NEW, "applying")
        (self.runtime / "deployed-ref").write_text(OLD + "\n")
        sent = []

        async def send(text):
            sent.append(text)

        await update_report.announce_update_outcome(send, self.runtime)
        self.assertEqual(coordinator.latest().phase, "quarantined")
        self.assertTrue((self.runtime / "auto-update-quarantine.json").exists())
        self.assertIn("v3.5.0", sent[0])

    async def test_corrupt_ledger_never_blocks_startup(self):
        (self.runtime / "release-ledger.sqlite3").write_bytes(b"not a database")
        self.assertIsNone(await update_report.announce_update_outcome(AsyncMock(), self.runtime))


class DetachedApplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        # mode=0o700 is umask-proof: the v3.3.0 journal hardening refuses
        # group/other-writable runtime dirs, and the default mkdir mode
        # inherits a permissive umask (e.g. 002) on CI and servers.
        (self.project / "runtime").mkdir(mode=0o700)

    async def test_apply_runs_in_its_own_unit_with_the_kill_switch_forwarded(self):
        launch = AsyncMock(return_value="opencode-bridge-update-1")

        async def sleep(_seconds):
            update_report.write_result(self.project / "runtime", status="updated",
                                       trigger="apply", tag="v3.5.0")

        with patch("bridge.services.host_control.launch_transient", launch), \
                patch.object(server_admin.asyncio, "sleep", sleep), \
                patch.dict(os.environ, {"OPENCODE_AUTO_UPDATE": "0", "TELEGRAM_BOT_TOKEN": "x"}):
            text = await server_admin._apply_bridge_detached(self.project)
        self.assertIn("v3.5.0", text)
        argv = launch.await_args.args[0]
        self.assertTrue(argv[-2].endswith("self_update.py"))
        self.assertEqual(argv[-1], "--apply")
        self.assertEqual(launch.await_args.kwargs["environment"], {"OPENCODE_AUTO_UPDATE": "0"})
        self.assertEqual(launch.await_args.kwargs["working_directory"], self.project)
        self.assertIsNone(update_report.read_result(self.project / "runtime"))  # consumed

    async def test_without_systemd_nothing_runs_in_process(self):
        launch = AsyncMock(side_effect=DetachedUnavailable("systemd-run is not available"))
        spawn = AsyncMock()
        with patch("bridge.services.host_control.launch_transient", launch), \
                patch.object(server_admin.asyncio, "create_subprocess_exec", spawn):
            text = await server_admin._apply_bridge_detached(self.project)
        self.assertIn("not started", text)
        self.assertIn("Nothing was changed", text)
        spawn.assert_not_called()

    async def test_if_the_bot_is_still_alive_without_a_result_it_explains_the_handoff(self):
        with patch("bridge.services.host_control.launch_transient",
                   AsyncMock(return_value="opencode-bridge-update-9")), \
                patch.object(server_admin, "_UPDATE_WAIT_SECONDS", 0):
            text = await server_admin._apply_bridge_detached(self.project)
        self.assertIn("opencode-bridge-update-9", text)
        self.assertIn("automatically", text)

    async def test_stale_result_from_an_earlier_run_is_not_mistaken_for_this_one(self):
        update_report.write_result(self.project / "runtime", status="updated", trigger="apply",
                                   tag="v1.0.0")
        path = update_report.result_path(self.project / "runtime")
        old = time.time() - 600
        os.utime(path, (old, old))
        real_sleep = asyncio.sleep
        polls = []

        async def short_sleep(_seconds):
            polls.append(1)
            await real_sleep(0.01)

        with patch("bridge.services.host_control.launch_transient", AsyncMock(return_value="u")), \
                patch.object(server_admin, "_UPDATE_WAIT_SECONDS", 0.2), \
                patch.object(server_admin.asyncio, "sleep", short_sleep):
            text = await server_admin._apply_bridge_detached(self.project)
        self.assertGreater(len(polls), 1, "the wait loop must actually poll for this test to mean anything")
        self.assertNotIn("v1.0.0", text)
        self.assertIn("running in the background", text)


class SelfUpdateConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # mode=0o700 is umask-proof: the v3.3.0 journal hardening refuses
        # group/other-writable runtime dirs, and the default mkdir mode
        # inherits a permissive umask (e.g. 002) on CI and servers.
        (self.root / "runtime").mkdir(mode=0o700)

    def test_default_is_unchanged(self):
        self.assertEqual(self_update.configured_repository(self.root, {}), "Alaa91H/opencode-bridge")

    def test_environment_wins_over_the_runtime_file(self):
        (self.root / "runtime" / "update-repository").write_text("file-owner/file-repo\n")
        self.assertEqual(self_update.configured_repository(self.root, {}), "file-owner/file-repo")
        self.assertEqual(self_update.configured_repository(
            self.root, {"OPENCODE_UPDATE_REPOSITORY": "env-owner/env-repo"}), "env-owner/env-repo")

    def test_malformed_or_hostile_values_are_rejected(self):
        for value in ("no-slash", "a/b/c", "../x/y", "o/../r", "o/r;rm -rf", "o/r r", "-o/r",
                      "https://evil.test/o/r", "o/" + "r" * 200):
            with self.assertRaises(ValueError, msg=value):
                self_update.configured_repository(self.root, {"OPENCODE_UPDATE_REPOSITORY": value})

    def test_symlinked_repository_file_is_refused(self):
        (self.root / "runtime" / "update-repository").symlink_to(self.root / "elsewhere")
        with self.assertRaises(ValueError):
            self_update.configured_repository(self.root, {})

    def test_api_urls_follow_the_configured_repository(self):
        self.assertTrue(self_update.RELEASE_API.startswith(self_update.API_PREFIX))
        self.assertTrue(self_update.ACTIONS_API.startswith(self_update.API_PREFIX))
        with self.assertRaises(ValueError):
            self_update.api_json("https://api.github.com/repos/someone-else/repo/releases/latest")

    def test_deploy_timeout_is_clamped(self):
        for raw, expected in (("420", 420), ("5", 60), ("99999", 1800), ("abc", 420)):
            with patch.dict(os.environ, {"OPENCODE_UPDATE_DEPLOY_TIMEOUT": raw}):
                self.assertEqual(self_update.deploy_timeout(), expected)

    def test_result_is_recorded_for_manual_runs_but_unattended_noise_is_not(self):
        self_update.record_result({"status": "up_to_date"}, trigger="auto", root=self.root)
        self.assertIsNone(update_report.read_result(self.root / "runtime"))
        self_update.record_result({"status": "up_to_date"}, trigger="apply", root=self.root)
        self.assertEqual(update_report.read_result(self.root / "runtime")["status"], "up_to_date")
        self_update.record_result({"status": "error", "reason": "x", "tag": "v3.5.0"},
                                  trigger="auto", root=self.root)
        self.assertEqual(update_report.read_result(self.root / "runtime")["status"], "error")

    def test_deploy_runs_through_bash_so_a_missing_executable_bit_cannot_quarantine_a_release(self):
        commit = "c" * 40
        (self.root / "VERSION").write_text("3.2.0\n", encoding="ascii")
        scripts = self.root / "scripts"
        scripts.mkdir()
        deploy = scripts / "deploy.sh"
        deploy.write_text('#!/usr/bin/env bash\nprintf "%s" "$1" > "$(dirname "$0")/../deployed-marker"\n')
        deploy.chmod(0o644)  # exactly what a zip extraction produced for the original deploy.sh
        calls = []

        def git(*args, cwd):
            return {"remote": "https://github.com/Alaa91H/opencode-bridge.git", "rev-parse": commit,
                    "show": "3.3.0"}.get(args[0], "")

        def api(url):
            if url == self_update.RELEASE_API:
                return {"tag_name": "v3.3.0", "draft": False, "prerelease": False}
            return {"workflow_runs": [{"head_sha": commit, "head_branch": "v3.3.0", "event": "push",
                                       "status": "completed", "conclusion": "success"}]}

        result = self_update.update(root=self.root, api=api, git_run=git, manual=True,
                                    deploy=lambda command: calls.append(command))
        self.assertEqual(result["status"], "updated")
        command = calls[0]
        self.assertEqual(command[0], "bash")
        self.assertEqual(Path(command[1]).name, "deploy.sh")
        self.assertEqual(command[-1], "v3.3.0")
        subprocess.run(command, check=True, timeout=10)  # would raise PermissionError if run directly
        self.assertEqual((self.root / "deployed-marker").read_text(), "v3.3.0")
        with self.assertRaises(PermissionError):
            subprocess.run([command[1], "x"], check=True, timeout=10)  # the pre-fix behaviour

    def test_unknown_status_is_never_recorded(self):
        self_update.record_result({"status": "mystery"}, trigger="apply", root=self.root)
        self.assertIsNone(update_report.read_result(self.root / "runtime"))


if __name__ == "__main__":
    unittest.main()
