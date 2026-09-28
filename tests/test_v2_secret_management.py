import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.infrastructure.security.secrets import (
    ChainedSecretSource, EnvironmentSecretSource, SystemdCredentialSource, exposed_secret,
)


class SecretManagementTests(unittest.TestCase):
    def test_systemd_credentials_precede_env_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"BOT_TOKEN": "legacy"}, clear=False):
            Path(tmp, "BOT_TOKEN").write_text("credential\n", encoding="utf-8")
            source = ChainedSecretSource(SystemdCredentialSource(Path(tmp)), EnvironmentSecretSource())
            self.assertEqual(source.read("BOT_TOKEN"), "credential")

    def test_env_remains_backward_compatible(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"BOT_TOKEN": "legacy"}, clear=False):
            source = ChainedSecretSource(SystemdCredentialSource(Path(tmp)), EnvironmentSecretSource())
            self.assertEqual(source.read("BOT_TOKEN"), "legacy")

    def test_missing_secret_and_scoped_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = SystemdCredentialSource(Path(tmp))
            self.assertIsNone(source.read("missing"))
            with self.assertRaises(KeyError):
                with exposed_secret(source, "missing"):
                    pass
