#!/usr/bin/env python3
"""Automatically deploy newer stable GitHub Releases after verified tag CI.

The existing tagged deploy script owns backup, health checking and rollback.
This entry point never follows branches, arbitrary URLs, or executes release
archives. It runs as the dedicated service user, never as root.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

EXPECTED_REPOSITORY = "alaa91h/opencode-bridge"
RELEASE_API = "https://api.github.com/repos/Alaa91H/opencode-bridge/releases/latest"
ACTIONS_API = "https://api.github.com/repos/Alaa91H/opencode-bridge/actions/workflows/quality-gate.yml/runs"
VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
PROJECT_DIR = Path(__file__).resolve().parents[1]


def normalize_github_repository(remote: str) -> str | None:
    value = remote.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    elif value.startswith(("https://", "ssh://")):
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


def version_of(tag: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.fullmatch(tag)
    return tuple(int(x) for x in match.groups()) if match else None


def api_json(url: str) -> dict:
    if not url.startswith("https://api.github.com/repos/Alaa91H/opencode-bridge/"):
        raise ValueError("Untrusted update API URL")
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "opencode-bridge-release-updater",
                      "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 -- trusted GitHub HTTPS endpoint
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid response")
    return payload


def git(*args: str, cwd: Path = PROJECT_DIR) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True,
        timeout=45)
    return result.stdout.strip()


def verified_ci(commit: str, tag: str, *, api=api_json) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    payload = api(f"{ACTIONS_API}?head_sha={commit}&per_page=100")
    return any(
        run.get("head_sha") == commit
        and run.get("head_branch") == tag
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        for run in payload.get("workflow_runs", [])
        if isinstance(run, dict)
    )


def update(*, root: Path = PROJECT_DIR, api=api_json, git_run=git,
           deploy=None) -> dict[str, str]:
    """Validate a stable release against the exact remote before activation."""
    if os.environ.get("OPENCODE_AUTO_UPDATE", "1") != "1":
        return {"status": "skipped", "reason": "disabled by OPENCODE_AUTO_UPDATE"}
    try:
        remote = git_run("remote", "get-url", "origin", cwd=root)
        if normalize_github_repository(remote) != EXPECTED_REPOSITORY:
            return {"status": "skipped", "reason": "unexpected Git remote"}
        if git_run("status", "--porcelain", "--untracked-files=no", cwd=root):
            return {"status": "skipped", "reason": "local tracked changes"}
        release = api(RELEASE_API)
        if release.get("draft") or release.get("prerelease"):
            return {"status": "skipped", "reason": "release is not stable"}
        tag = release.get("tag_name")
        candidate = version_of(tag) if isinstance(tag, str) else None
        if candidate is None:
            return {"status": "skipped", "reason": "release tag is not stable semver"}
        version_text = (root / "VERSION").read_text(encoding="utf-8").strip()
        current = version_of("v" + version_text)
        if current is None:
            return {"status": "skipped", "reason": "installed version is not stable semver"}
        if candidate <= current:
            return {"status": "up_to_date", "tag": tag}
        # Never trust a tag name from an arbitrary repository or branch.
        git_run("fetch", "--no-tags", "origin", f"refs/tags/{tag}", cwd=root)
        commit = git_run("rev-parse", "FETCH_HEAD^{commit}", cwd=root)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("bad tag commit")
        actual = git_run("show", f"{commit}:VERSION", cwd=root).strip()
        if actual != tag.removeprefix("v"):
            return {"status": "skipped", "reason": "release tag and VERSION disagree"}
        if not verified_ci(commit, tag, api=api):
            return {"status": "skipped", "reason": "tagged CI not successful yet"}
        command = [str(root / "scripts" / "deploy.sh"), tag]
        if deploy is None:
            subprocess.run(command, cwd=root, check=True, timeout=240,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        else:
            deploy(command)
        return {"status": "updated", "tag": tag, "commit": commit}
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            urllib.error.URLError, KeyError) as exc:
        # Do not include subprocess stderr or full URLs containing credentials.
        return {"status": "error", "reason": type(exc).__name__}


def main() -> int:
    result = update()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"updated", "up_to_date", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
