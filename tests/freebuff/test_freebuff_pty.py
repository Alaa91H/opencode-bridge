from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bridge.infrastructure.freebuff.client import FreebuffBackend
from bridge.infrastructure.freebuff.pty_runner import PTY_SUPPORTED

FAKE_CLI = '''#!/usr/bin/env python3
import json
import os
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("freebuff 0.0.200", flush=True)
    sys.exit(0)

print("Freebuff ready", flush=True)
print("\u276f", flush=True)
line = sys.stdin.readline()
config = os.environ.get("FREEBUFF_CONFIG_DIR", "")
chat = os.path.join(config, "projects", "p1", "chats", "chat-1")
os.makedirs(chat, exist_ok=True)
with open(os.path.join(chat, "chat-messages.json"), "w", encoding="utf-8") as handle:
    json.dump(
        {
            "messages": [
                {"role": "user", "content": line.strip() or "prompt"},
                {"role": "assistant", "content": "الإجابة النهائية من Freebuff", "completed": True},
            ]
        },
        handle,
        ensure_ascii=False,
    )
print("Done", flush=True)
print("\u276f", flush=True)
time.sleep(30)
'''


class FakeSessions:
    def __init__(self) -> None:
        self.saved = []

    async def get_session(self, owner_id, backend, workspace_key):
        return None

    async def delete_session(self, owner_id, backend, workspace_key):
        return False

    async def upsert_session(self, owner_id, backend, workspace_key, session_id, model=None):
        self.saved.append((owner_id, backend, workspace_key, session_id, model))


@unittest.skipUnless(PTY_SUPPORTED, "Freebuff PTY harness is Unix-only")
class FreebuffPtyTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_is_executed_and_answer_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            config = root / "config"
            config.mkdir()
            (config / "credentials.json").write_text("{}", encoding="utf-8")
            binary = root / "freebuff"
            binary.write_text(FAKE_CLI, encoding="utf-8")
            binary.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)

            settings = SimpleNamespace(
                bin=str(binary),
                config_dir=str(config),
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
            sessions = FakeSessions()
            backend = FreebuffBackend(
                settings=settings,
                sessions=sessions,
                default_workspace=workspace,
            )
            try:
                response = await backend.send_prompt(
                    "",
                    "اكتب إجابة قصيرة",
                    owner_id="7",
                )
            finally:
                await backend.close()

            self.assertEqual(response["backend"], "freebuff")
            self.assertEqual(response["parts"][0]["text"], "الإجابة النهائية من Freebuff")
            self.assertEqual(sessions.saved[0][3], "chat-1")
            self.assertFalse(any((workspace / ".bridge" / "prompts").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
