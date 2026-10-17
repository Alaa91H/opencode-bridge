"""Regression tests for the owner-only direct shell service."""

from __future__ import annotations

import unittest

from bridge.services.shell_exec import (
    MAX_COMMAND_CHARS,
    ShellResult,
    format_result,
    policy_advisory,
    run_shell,
    validate_command,
)


class ValidateCommandTests(unittest.TestCase):
    def test_empty_command_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_command("")
        with self.assertRaises(ValueError):
            validate_command("   ")

    def test_oversized_and_nul_commands_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_command("x" * (MAX_COMMAND_CHARS + 1))
        with self.assertRaises(ValueError):
            validate_command("echo \x00 hi")

    def test_plain_command_is_stripped_not_rewritten(self) -> None:
        self.assertEqual(validate_command("  df -h  "), "df -h")


class FormatResultTests(unittest.TestCase):
    def test_success_and_failure_markers(self) -> None:
        self.assertIn("✅", format_result("df", ShellResult(0, "ok", False)))
        failed = format_result("false", ShellResult(1, "boom", False))
        self.assertIn("❌ code 1", failed)
        self.assertIn("boom", failed)


class PolicyAdvisoryTests(unittest.TestCase):
    def test_safe_command_has_no_advisory(self) -> None:
        self.assertIsNone(policy_advisory("df -h"))
        self.assertIsNone(policy_advisory("echo hello"))

    def test_hardline_match_warns_without_blocking(self) -> None:
        advisory = policy_advisory("rm -rf /")
        self.assertIsNotNone(advisory)
        assert advisory is not None
        self.assertIn("⚠️", advisory)

    def test_build_match_notes_server_side_effect(self) -> None:
        advisory = policy_advisory("pip install requests")
        self.assertIsNotNone(advisory)
        assert advisory is not None
        self.assertIn("ℹ️", advisory)


class LiveShellTests(unittest.TestCase):
    def test_echo_round_trip(self) -> None:
        import asyncio

        result = asyncio.run(run_shell("echo hello", timeout=15.0))
        self.assertEqual(result.code, 0)
        self.assertIn("hello", result.output)


if __name__ == "__main__":
    unittest.main()
