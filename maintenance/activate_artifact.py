#!/usr/bin/env python3
"""Activate an already built artifact on the production host with rollback."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from artifact_activation import ArtifactActivator

BRIDGE_DIR = Path("/home/ubuntu/opencode-bridge")
RELEASE_HOME = Path("/home/ubuntu/opencode-releases")
CURRENT = RELEASE_HOME / "current"
RUNTIME = BRIDGE_DIR / "runtime"
DATABASE = BRIDGE_DIR / "sessions.db"
ENV_FILE = BRIDGE_DIR / ".env"
REPOSITORY = "Alaa91H/opencode-bridge"


class DeploymentError(RuntimeError):
    pass


def run(*argv: str, timeout: float = 45.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(list(argv), text=True, capture_output=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise DeploymentError(f"{argv[0]} failed with exit code {result.returncode}")
    return result


def assert_latest_main(source_sha: str) -> None:
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/commits/main",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "opencode-bridge-deployer"},
    )
    with urlopen(request, timeout=15) as response:
        latest = json.load(response).get("sha")
    if latest != source_sha:
        raise DeploymentError("artifact is stale; main has advanced since its CI run")


def check_resources() -> None:
    memory = Path("/proc/meminfo").read_text(encoding="ascii")
    available_line = next((line for line in memory.splitlines() if line.startswith("MemAvailable:")), None)
    if available_line is None or int(available_line.split()[1]) < 128 * 1024:
        raise DeploymentError("insufficient available memory to stage a safe deployment")
    if shutil.disk_usage(RELEASE_HOME.parent).free < 1024 * 1024 * 1024:
        raise DeploymentError("insufficient free disk space to preserve rollback releases")


def quiesce_bridge() -> None:
    run("systemctl", "--user", "stop", "opencode-bridge-telegram.service", timeout=40)
    state = run("systemctl", "--user", "is-active", "opencode-bridge-telegram.service", check=False)
    if state.returncode == 0 and state.stdout.strip() == "active":
        raise DeploymentError("Telegram worker did not stop before the database snapshot")


def service_restart() -> None:
    run("systemctl", "--user", "daemon-reload")
    run("systemctl", "--user", "--no-block", "restart", "opencode-bridge.target")


def service_smoke(_release: Path) -> None:
    if not (BRIDGE_DIR / "venv" / "bin" / "python").is_file():
        raise DeploymentError("prepared production Python environment is missing")
    curl_command = (
        'set -a; source "$1"; '
        'curl --fail --silent --show-error --max-time 5 '
        '--user "${OPENCODE_SERVER_USERNAME:-opencode}:${OPENCODE_SERVER_PASSWORD}" '
        '"http://${OPENCODE_HOST:-127.0.0.1}:${OPENCODE_PORT:-4096}/global/health" >/dev/null'
    )
    deadline = time.monotonic() + 40
    last_error = "services are not ready"
    while time.monotonic() < deadline:
        bot = run("systemctl", "--user", "is-active", "opencode-bridge-telegram.service", check=False)
        server = run("systemctl", "--user", "is-active", "opencode-serve.service", check=False)
        if bot.returncode == 0 and bot.stdout.strip() == "active" and server.returncode == 0 and server.stdout.strip() == "active":
            health = run("bash", "-c", curl_command, "_", str(ENV_FILE), timeout=8, check=False)
            if health.returncode == 0:
                return
            last_error = "OpenCode health endpoint did not pass"
        time.sleep(2)
    raise DeploymentError(f"readiness/smoke checks timed out: {last_error}")


def activate(archive: Path, manifest: Path, source_sha: str, version: str) -> Path:
    assert_latest_main(source_sha)
    check_resources()
    python = BRIDGE_DIR / "venv" / "bin" / "python"
    run(str(python), str(BRIDGE_DIR / "scripts" / "check_queue.py"), timeout=20)

    def prepare(release: Path) -> None:
        run(str(python), str(release / "systemd.py"), timeout=30)
        run("sudo", "-n", str(release / "maintenance" / "install-root-assets.sh"), timeout=45)

    def commit_identity(_release: Path) -> None:
        record = {
            "tag": f"v{version}",
            "version": version,
            "commit": source_sha,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = RUNTIME / ".deployment-identity.tmp"
        identity = RUNTIME / "deployment-identity"
        temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(identity)

    activator = ArtifactActivator(
        RELEASE_HOME / "releases",
        CURRENT,
        BRIDGE_DIR,
        RUNTIME,
        DATABASE,
        RELEASE_HOME / "backups",
    )
    release = activator.activate(
        archive,
        manifest,
        source_sha,
        version,
        prepare=prepare,
        restart=service_restart,
        smoke=service_smoke,
        commit=commit_identity,
        quiesce=quiesce_bridge,
    )
    print(f"deployment=passed sha={source_sha} version={version} release={release}")
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        activate(args.archive, args.manifest, args.source_sha, args.version)
    except (OSError, ValueError, subprocess.SubprocessError, DeploymentError) as error:
        print(f"deployment=failed reason={error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
