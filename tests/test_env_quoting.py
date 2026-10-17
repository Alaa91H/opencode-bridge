"""Regression tests for portable `.env` credential quoting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.services.host_credentials import HostCredentialManager, quote_env_value


class QuoteEnvValueTests(unittest.TestCase):
    def test_plain_tokens_stay_bare(self) -> None:
        self.assertEqual(quote_env_value("validSecret123"), "validSecret123")
        self.assertEqual(quote_env_value("123456:ABC-def123"), "123456:ABC-def123")
        self.assertEqual(quote_env_value("ghp_abCD1234"), "ghp_abCD1234")

    def test_spaces_and_hashes_are_quoted(self) -> None:
        self.assertEqual(quote_env_value("a b#c"), '"a b#c"')
        self.assertEqual(quote_env_value("p@ss w0rd!"), '"p@ss w0rd!"')

    def test_quotes_and_backslashes_are_escaped(self) -> None:
        self.assertEqual(quote_env_value('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(quote_env_value("back\\slash"), '"back\\\\slash"')


class QuotedCredentialRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_spaced_secret_is_quoted_in_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = HostCredentialManager(root)
            await manager.save("github", "spaced", "my secret 123")
            await manager.activate("github", "spaced")
            self.assertIn('GITHUB_TOKEN="my secret 123"', (root / ".env").read_text())


if __name__ == "__main__":
    unittest.main()
