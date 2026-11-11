from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.infrastructure.freebuff.binary import (
    ProbeConfig,
    child_env,
    credentials_present,
    parse_version,
    probe,
    resolve_binary,
    resolve_config_dir,
    version_at_least,
)


class VersionTests(unittest.TestCase):
    def test_parse_version_from_launcher_output(self) -> None:
        self.assertEqual(parse_version("freebuff 0.0.163\n"), (0, 0, 163))
        self.assertEqual(parse_version("v3.2.1"), (3, 2, 1))
        self.assertIsNone(parse_version("no version here"))

    def test_minimum_comparison(self) -> None:
        self.assertTrue(version_at_least((0, 0, 140), (0, 0, 140)))
        self.assertTrue(version_at_least((0, 1, 0), (0, 0, 140)))
        self.assertFalse(version_at_least((0, 0, 139), (0, 0, 140)))
        self.assertFalse(version_at_least(None, (0, 0, 140)))

    def test_explicit_config_dir_wins(self) -> None:
        self.assertEqual(resolve_config_dir("/tmp/custom"), Path("/tmp/custom"))

    def test_credentials_are_detected_by_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)
            self.assertFalse(credentials_present(config))
            (config / "credentials.json").write_text("{}", encoding="utf-8")
            self.assertTrue(credentials_present(config))

    def test_child_env_passes_keyring_variables_but_not_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "secret",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                "DISPLAY": ":0",
                "FREEBUFF_CONFIG_DIR": "/wrong",
            },
            clear=True,
        ):
            env = child_env(Path("/cfg"))
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["FREEBUFF_CONFIG_DIR"], str(Path("/cfg")))

    def test_explicit_binary_path_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            binary = Path(raw) / "freebuff"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            self.assertEqual(resolve_binary(str(binary), Path(raw)), binary)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_binary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = await probe(
                ProbeConfig(binary=str(Path(raw) / "missing"), config_dir=raw)
            )
        self.assertFalse(result.supported)
        self.assertFalse(result.healthy)
        self.assertEqual(result.reason, "not_installed")


if __name__ == "__main__":
    unittest.main()
