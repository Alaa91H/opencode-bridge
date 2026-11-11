from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

from bridge.infrastructure.freebuff.pty_runner import PTY_SUPPORTED, PtyProcess


@unittest.skipUnless(PTY_SUPPORTED, "PTY execution is Unix-only")
class PtyRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip_and_clean_termination(self) -> None:
        code = (
            "import sys, time;"
            "print('ready', flush=True);"
            "line = sys.stdin.readline();"
            "print('got:' + line.strip(), flush=True);"
            "time.sleep(5)"
        )
        process = await PtyProcess.start(
            [sys.executable, "-u", "-c", code],
            cwd=Path.cwd(),
            env=dict(os.environ),
        )
        output = b""
        try:
            deadline = time.monotonic() + 10
            while b"ready" not in output and time.monotonic() < deadline:
                output += await process.read(0.5)
            self.assertIn(b"ready", output)
            await process.write("hello\r")
            while b"got:hello" not in output and time.monotonic() < deadline:
                output += await process.read(0.5)
            self.assertIn(b"got:hello", output)
        finally:
            await process.terminate(grace=0.5)


if __name__ == "__main__":
    unittest.main()
