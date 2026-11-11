from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.infrastructure.freebuff.binary import FreebuffInstallation
from bridge.infrastructure.freebuff.client import FreebuffBackend


class FakeSessions:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str, str, str | None]] = []

    async def get_session(self, owner_id, backend, workspace_key):
        return None

    async def delete_session(self, owner_id, backend, workspace_key):
        return False

    async def upsert_session(self, owner_id, backend, workspace_key, session_id, model=None):
        self.saved.append((owner_id, backend, workspace_key, session_id, model))


def _settings(**overrides):
    values = {
        "bin": "",
        "config_dir": "",
        "workspace": "",
        "model": "",
        "models": (),
        "model_write": False,
        "trust_agents": False,
        "idle_seconds": 25.0,
        "startup_seconds": 90.0,
        "timeout_seconds": 3600.0,
        "export_seconds": 60.0,
        "min_version": "0.0.140",
        "max_output_chars": 120000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _installation(config: Path, binary: Path | None = None) -> FreebuffInstallation:
    return FreebuffInstallation(
        path=binary or config / "freebuff",
        version=(0, 0, 200),
        version_text="0.0.200",
        config_dir=config,
        credentials_present=True,
        supported=True,
        reason="ok",
    )


class FreebuffHelperTests(unittest.TestCase):
    def test_stage_prompt_writes_instruction_and_copies_file_parts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            source = root / "report.pdf"
            source.write_bytes(b"%PDF-1.4")
            backend = FreebuffBackend(
                settings=_settings(),
                sessions=FakeSessions(),
                default_workspace=workspace,
            )
            staged = backend._stage_prompt(
                "حلّل الملف",
                [{"type": "file", "url": source.as_uri()}],
                owner_id="1",
            )
            try:
                content = staged.prompt_path.read_text(encoding="utf-8")
                self.assertIn("حلّل الملف", content)
                self.assertIn("report.pdf", content)
                assert staged.attachments_dir is not None
                self.assertTrue(any(staged.attachments_dir.iterdir()))
            finally:
                staged.prompt_path.unlink(missing_ok=True)
                if staged.attachments_dir:
                    import shutil

                    shutil.rmtree(staged.attachments_dir, ignore_errors=True)

    def test_build_argv_uses_cwd_and_rejects_unsafe_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)
            backend = FreebuffBackend(
                settings=_settings(),
                sessions=FakeSessions(),
                default_workspace=config,
            )
            argv = backend._build_argv(_installation(config), "")
            self.assertEqual(argv[1:3], ["--cwd", str(backend.workspace)])
            self.assertNotIn("--continue", argv)
            unsafe = backend._build_argv(_installation(config), "--evil")
            self.assertNotIn("--continue", unsafe)
            safe = backend._build_argv(_installation(config), "chat-1")
            self.assertIn("--continue", safe)

    def test_trust_agents_requires_explicit_setting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)
            backend = FreebuffBackend(
                settings=_settings(trust_agents=True),
                sessions=FakeSessions(),
                default_workspace=config,
            )
            self.assertIn("--trust-agents", backend._build_argv(_installation(config), ""))

    def test_model_write_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)
            backend = FreebuffBackend(
                settings=_settings(),
                sessions=FakeSessions(),
                default_workspace=config,
            )
            backend._apply_model(_installation(config), "model-a")
            self.assertFalse((config / "settings.json").exists())

    def test_models_prefers_configured_list(self) -> None:
        backend = FreebuffBackend(
            settings=_settings(models=("a", "b"), model="a"),
            sessions=FakeSessions(),
            default_workspace=Path.cwd(),
        )
        self.assertEqual(backend.models(), ("a", "b"))


if __name__ == "__main__":
    unittest.main()
