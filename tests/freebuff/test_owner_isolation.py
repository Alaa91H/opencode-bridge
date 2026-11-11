from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.infrastructure.freebuff.chat_store import find_latest_chat
from bridge.infrastructure.freebuff.client import FreebuffBackend, _Run
from bridge.infrastructure.freebuff.screen import ScreenAnalyzer


class FakeSessions:
    async def get_session(self, owner_id, backend, workspace_key):
        return None

    async def delete_session(self, owner_id, backend, workspace_key):
        return False

    async def upsert_session(self, owner_id, backend, workspace_key, session_id, model=None):
        return None


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False

    async def terminate(self, grace: float = 1.0) -> None:
        self.terminated = True


def _settings(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        bin="",
        config_dir="",
        workspace=str(workspace),
        model="",
        models=(),
        model_write=False,
        trust_agents=False,
        idle_seconds=3.0,
        startup_seconds=10.0,
        timeout_seconds=60.0,
        export_seconds=5.0,
        min_version="0.0.1",
        max_output_chars=120000,
    )


class OwnerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_abort_only_matches_the_owner_who_started_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            backend = FreebuffBackend(
                settings=_settings(workspace),
                sessions=FakeSessions(),
                default_workspace=workspace,
            )
            process = FakeProcess()
            run = _Run(
                session_id="chat-1",
                screen=ScreenAnalyzer(),
                owner_id="7",
                prompt_marker="",
                process=process,
            )
            backend._runs[("7", "chat-1")] = run

            self.assertFalse(await backend.abort_session("chat-1", owner_id="8"))
            self.assertFalse(run.cancelled)
            self.assertTrue(await backend.abort_session("chat-1", owner_id="7"))
            self.assertTrue(run.cancelled)
            self.assertTrue(process.terminated)


class ChatMarkerTests(unittest.TestCase):
    def test_newer_unrelated_chat_is_skipped_when_a_marker_is_given(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw)

            def write(chat_id: str, text: str) -> None:
                chat = config / "projects" / "p" / "chats" / chat_id
                chat.mkdir(parents=True, exist_ok=True)
                (chat / "chat-messages.json").write_text(
                    json.dumps(
                        {"messages": [{"role": "user", "content": text}]},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            write("ours", "اقرأ /tmp/run-ours.md")
            write("other", "اقرأ /tmp/run-other.md")

            ref = find_latest_chat(config, since=0, contains="run-ours")
            self.assertIsNotNone(ref)
            assert ref is not None
            self.assertEqual(ref.chat_id, "ours")
            self.assertIsNone(find_latest_chat(config, since=0, contains="missing"))


if __name__ == "__main__":
    unittest.main()
