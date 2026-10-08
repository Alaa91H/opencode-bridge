#!/usr/bin/env python3
"""Safe release-based self-update for the deployed OpenCode Bridge checkout."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

BRIDGE_DIR = Path(__file__).resolve().parents[1]
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


def _run(*args: str, cwd: Path = BRIDGE_DIR, timeout: float = 300.0, check: bool = True):
    result = subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip().splitlines()
        raise UpdateError(detail[-1][:500] if detail else "command failed")
    return result


def _git(*args: str, cwd: Path = BRIDGE_DIR, timeout: float = 300.0, check: bool = True):
    return _run("git", *args, cwd=cwd, timeout=timeout, check=check)


def latest_stable_tag() -> str:
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for tag in _git("tag", "--list").stdout.splitlines():
        match = _STABLE_TAG.fullmatch(tag.strip())
        if match:
            candidates.append((tuple(int(v) for v in match.groups()), tag.strip()))
    if not candidates:
        raise UpdateError("no stable release tag is available")
    return max(candidates)[1]


def update() -> dict[str, object]:
    """Move only to a stable release whose CI gates ran before publication.

    Compilation and tests run in GitHub Actions. Production checks only release
    identity, queue safety, and runtime health; it never builds or runs tests.
    """
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
        return {"status": "skipped", "reason": "stable release is not on trusted main history", "target_tag": target_tag}

    local_sha = _git("rev-parse", "HEAD").stdout.strip()
    if local_sha == target_sha:
        return {"status": "up_to_date", "from": local_sha, "to": target_sha, "target_tag": target_tag}
    if _git("merge-base", "--is-ancestor", local_sha, target_sha, check=False).returncode != 0:
        return {"status": "skipped", "reason": "current checkout is not an ancestor of stable release", "from": local_sha, "to": target_sha, "target_tag": target_tag}

    # Keep user data and venv untouched. Validate the target tree is complete
    # without running compileall, installing packages, or executing tests here.
    required_files = ("VERSION", "run_v3.py", "wait_for_opencode.py", "requirements.lock")
    for name in required_files:
        if _git("cat-file", "-e", f"{target_ref}:{name}", check=False).returncode != 0:
            return {"status": "skipped", "reason": f"stable release is missing required file: {name}", "target_tag": target_tag}

    # Never move a deployment while a queued task is active.
    _run(str(BRIDGE_DIR / "venv" / "bin" / "python"), "scripts/check_queue.py", cwd=BRIDGE_DIR, timeout=30)
    _git("checkout", "--detach", "--quiet", target_sha, timeout=120)
    applied_sha = _git("rev-parse", "HEAD").stdout.strip()
    if applied_sha != target_sha:
        raise UpdateError("failed to activate the verified stable release commit")
    return {
        "status": "updated",
        "from": local_sha,
        "to": applied_sha,
        "target_tag": target_tag,
        "deployment_mode": "detached_release",
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
