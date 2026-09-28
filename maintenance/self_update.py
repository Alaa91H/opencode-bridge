#!/usr/bin/env python3
"""Safe release-based self-update for the deployed OpenCode Bridge checkout."""

from __future__ import annotations

import json
import re
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
_STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


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


def _nul_paths(output: str) -> set[str]:
    return {item for item in output.split("\0") if item}


def _untracked_paths() -> set[str]:
    return _nul_paths(_git("ls-files", "--others", "--exclude-standard", "-z").stdout)


def _tracked_paths(ref: str) -> set[str]:
    return _nul_paths(_git("ls-tree", "-r", "--name-only", "-z", ref).stdout)


def _untracked_conflicts(ref: str) -> set[str]:
    return _untracked_paths() & _tracked_paths(ref)


def latest_stable_tag() -> str:
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for tag in _git("tag", "--list").stdout.splitlines():
        match = _STABLE_TAG.fullmatch(tag.strip())
        if match:
            candidates.append((tuple(int(v) for v in match.groups()), tag.strip()))
    if not candidates:
        raise UpdateError("no stable release tag is available")
    return max(candidates)[1]


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
    _run(str(PYTHON_BIN), "-m", "json.tool", "opencode.json", cwd=candidate, timeout=30)
    shell_files = [candidate / "start.sh", *sorted((candidate / "maintenance").glob("*.sh"))]
    for shell_file in shell_files:
        if shell_file.is_file():
            _run("bash", "-n", str(shell_file), cwd=candidate, timeout=30)


def update() -> dict[str, object]:
    origin = _git("remote", "get-url", "origin").stdout.strip()
    if normalize_github_repository(origin) != EXPECTED_REPOSITORY:
        raise UpdateError("origin is not the trusted Alaa91H/opencode-bridge repository")

    tracked_dirty = _git("status", "--porcelain=v1", "--untracked-files=no").stdout.strip()
    if tracked_dirty:
        return {"status": "skipped", "reason": "tracked files contain local changes"}

    _git("fetch", "--prune", "--tags", "origin", UPDATE_BRANCH, timeout=180)
    target_tag = latest_stable_tag()
    target_ref = f"refs/tags/{target_tag}"
    target_sha = _git("rev-parse", f"{target_ref}^{{commit}}").stdout.strip()
    remote_sha = _git("rev-parse", f"origin/{UPDATE_BRANCH}").stdout.strip()

    if _git("merge-base", "--is-ancestor", target_sha, remote_sha, check=False).returncode != 0:
        return {
            "status": "skipped",
            "reason": "latest stable release is not on trusted origin/main history",
            "target_tag": target_tag,
        }

    conflicts = sorted(_untracked_conflicts(target_ref))
    if conflicts:
        return {
            "status": "skipped",
            "reason": "untracked files would conflict with the target release",
            "conflicts": conflicts[:20],
            "target_tag": target_tag,
        }

    local_sha = _git("rev-parse", "HEAD").stdout.strip()
    if local_sha == target_sha:
        return {
            "status": "up_to_date",
            "from": local_sha,
            "to": target_sha,
            "target_tag": target_tag,
            "deployment_mode": "detached_release",
            "preserved_untracked": len(_untracked_paths()),
        }

    if _git("merge-base", "--is-ancestor", local_sha, target_sha, check=False).returncode != 0:
        return {
            "status": "skipped",
            "reason": "current checkout is not an ancestor of the latest stable release",
            "from": local_sha,
            "to": target_sha,
            "target_tag": target_tag,
        }

    temp_root = Path(tempfile.mkdtemp(prefix="opencode-bridge-update-"))
    candidate = temp_root / "candidate"
    try:
        _git("worktree", "add", "--detach", str(candidate), target_sha, timeout=120)
        try:
            _validate_candidate(candidate)
        finally:
            _git("worktree", "remove", "--force", str(candidate), timeout=120, check=False)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    if _git("rev-parse", "HEAD").stdout.strip() != local_sha:
        raise UpdateError("local HEAD changed while the update was being validated")
    if _git("status", "--porcelain=v1", "--untracked-files=no").stdout.strip():
        raise UpdateError("tracked files changed while the update was being validated")
    if _untracked_conflicts(target_ref):
        raise UpdateError("untracked files became conflicting while the update was being validated")

    # Do not move a deployment while a queued task is actively running.
    _run(str(PYTHON_BIN), "scripts/check_queue.py", cwd=BRIDGE_DIR, timeout=30)

    _git("checkout", "--detach", "--quiet", target_sha, timeout=120)
    applied_sha = _git("rev-parse", "HEAD").stdout.strip()
    if applied_sha != target_sha:
        raise UpdateError("failed to activate the validated release commit")

    return {
        "status": "updated",
        "from": local_sha,
        "to": applied_sha,
        "target_tag": target_tag,
        "deployment_mode": "detached_release",
        "preserved_untracked": len(_untracked_paths()),
    }


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
