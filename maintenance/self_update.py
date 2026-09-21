#!/usr/bin/env python3
"""Safe fast-forward self-update for the checked-out OpenCode Bridge repository."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

BRIDGE_DIR = Path(__file__).resolve().parents[1]
PYTHON_BIN = BRIDGE_DIR / "venv" / "bin" / "python"
EXPECTED_REPOSITORY = "alaa91h/opencode-bridge"
UPDATE_BRANCH = "main"


class UpdateError(RuntimeError):
    pass


def normalize_github_repository(remote: str) -> str | None:
    value = remote.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    elif value.startswith(("https://", "http://", "ssh://")):
        parsed = urlparse(value)
        if (parsed.hostname or "").casefold() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    else:
        return None
    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return f"{parts[0]}/{parts[1]}".casefold()


def _run(
    *args: str,
    cwd: Path = BRIDGE_DIR,
    timeout: float = 300.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip().splitlines()
        raise UpdateError(detail[-1][:500] if detail else "command failed")
    return result


def _git(*args: str, cwd: Path = BRIDGE_DIR, timeout: float = 300.0, check: bool = True):
    return _run("git", *args, cwd=cwd, timeout=timeout, check=check)


def _validate_candidate(candidate: Path) -> None:
    if not PYTHON_BIN.is_file():
        raise UpdateError("prepared Python environment is missing")

    _run(str(PYTHON_BIN), "-m", "compileall", "-q", ".", cwd=candidate, timeout=300)
    _run(
        str(PYTHON_BIN),
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*.py",
        "-v",
        cwd=candidate,
        timeout=900,
    )
    for config_name in ("opencode.json", "opencode-v3.json"):
        _run(str(PYTHON_BIN), "-m", "json.tool", config_name, cwd=candidate, timeout=30)
    shell_files = [candidate / "start.sh", *sorted((candidate / "maintenance").glob("*.sh"))]
    for shell_file in shell_files:
        if shell_file.is_file():
            _run("bash", "-n", str(shell_file), cwd=candidate, timeout=30)


def update() -> dict[str, object]:
    origin = _git("remote", "get-url", "origin").stdout.strip()
    normalized = normalize_github_repository(origin)
    if normalized != EXPECTED_REPOSITORY:
        raise UpdateError("origin is not the trusted Alaa91H/opencode-bridge repository")

    branch = _git("branch", "--show-current").stdout.strip()
    if branch != UPDATE_BRANCH:
        return {"status": "skipped", "reason": f"current branch is {branch or 'detached'}, expected {UPDATE_BRANCH}"}

    dirty = _git("status", "--porcelain=v1", "--untracked-files=normal").stdout.strip()
    if dirty:
        return {"status": "skipped", "reason": "working tree contains local changes"}

    _git("fetch", "--prune", "origin", UPDATE_BRANCH, timeout=180)
    local_sha = _git("rev-parse", "HEAD").stdout.strip()
    remote_sha = _git("rev-parse", f"origin/{UPDATE_BRANCH}").stdout.strip()
    if local_sha == remote_sha:
        return {"status": "up_to_date", "from": local_sha, "to": remote_sha}

    ancestor = _git("merge-base", "--is-ancestor", local_sha, remote_sha, check=False)
    if ancestor.returncode != 0:
        return {"status": "skipped", "reason": "local branch diverged from origin/main", "from": local_sha, "to": remote_sha}

    temp_root = Path(tempfile.mkdtemp(prefix="opencode-bridge-update-"))
    candidate = temp_root / "candidate"
    try:
        _git("worktree", "add", "--detach", str(candidate), remote_sha, timeout=120)
        try:
            _validate_candidate(candidate)
        finally:
            _git("worktree", "remove", "--force", str(candidate), timeout=120, check=False)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    # Re-check immediately before changing the checked-out deployment.
    if _git("rev-parse", "HEAD").stdout.strip() != local_sha:
        raise UpdateError("local HEAD changed while the update was being validated")
    if _git("status", "--porcelain=v1", "--untracked-files=normal").stdout.strip():
        raise UpdateError("working tree changed while the update was being validated")

    _git("merge", "--ff-only", f"origin/{UPDATE_BRANCH}", timeout=120)
    applied_sha = _git("rev-parse", "HEAD").stdout.strip()
    return {"status": "updated", "from": local_sha, "to": applied_sha}


def main() -> int:
    try:
        result = update()
    except (OSError, subprocess.SubprocessError, UpdateError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
