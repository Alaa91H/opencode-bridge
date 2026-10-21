"""systemd ExecStartPre gate: wait until local OpenCode is healthy.

Prevents the bridge (and its watchdog) from starting against a still-booting
OpenCode server, which previously caused a restart loop after every reboot.
Reads credentials from the bridge .env file. Exits 0 when healthy, 1 on timeout.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

BRIDGE_DIR = Path(__file__).parent
TIMEOUT_SECONDS = 150

env: dict[str, str] = {}
env_path = BRIDGE_DIR / ".env"
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()

host = env.get("OPENCODE_HOST", "127.0.0.1")
port = env.get("OPENCODE_PORT", "4096")
password = env.get("OPENCODE_SERVER_PASSWORD") or env.get("OPENCODE_PASSWORD") or ""
token = base64.b64encode(f"opencode:{password}".encode()).decode()
url = f"http://{host}:{port}/global/health"

deadline = time.monotonic() + TIMEOUT_SECONDS
attempt = 0
while time.monotonic() < deadline:
    attempt += 1
    try:
        request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
            if isinstance(payload, dict) and payload.get("healthy"):
                print(f"opencode healthy after {attempt} attempt(s)")
                sys.exit(0)
    except Exception as exc:
        print(f"waiting for opencode... ({type(exc).__name__})", flush=True)
    time.sleep(5)

print("opencode did not become healthy in time", file=sys.stderr)
sys.exit(1)
