#!/usr/bin/env python3
"""Automatically deploy newer stable GitHub Releases after verified tag CI.

The existing tagged deploy script owns backup, health checking and rollback.
This entry point never follows branches, arbitrary URLs, or executes release
archives. It runs as the dedicated service user, never as root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

EXPECTED_REPOSITORY = "alaa91h/opencode-bridge"
RELEASE_API = "https://api.github.com/repos/Alaa91H/opencode-bridge/releases/latest"
ACTIONS_API = "https://api.github.com/repos/Alaa91H/opencode-bridge/actions/workflows/quality-gate.yml/runs"
VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
PROJECT_DIR = Path(__file__).resolve().parents[1]
QUARANTINE_FILE = "auto-update-quarantine.json"
AUTO_MODES = ("off", "notify", "auto-safe", "maintenance-window")


def deployment_mode(root: Path) -> str:
    """Read owner policy; fail closed if local runtime controls are invalid."""
    path = root / "runtime" / "autopilot-mode"
    if path.is_symlink():
        raise ValueError("unsafe autopilot policy")
    if path.exists():
        mode = path.read_text(encoding="ascii").strip()
        if mode not in AUTO_MODES:
            raise ValueError("invalid autopilot policy")
        return mode
    legacy = root / "runtime" / "auto-update-enabled"
    if legacy.is_symlink():
        raise ValueError("unsafe legacy update policy")
    if not legacy.exists():
        return "auto-safe"
    return "off" if legacy.read_text(encoding="ascii").strip() == "0" else "auto-safe"


def maintenance_window_open(now: datetime | None = None) -> bool:
    """Daily 08:00–10:00 UTC, independent of local DST and server timezone."""
    hour = (now or datetime.now(UTC)).astimezone(UTC).hour
    return 8 <= hour < 10



def _quarantine_path(root: Path) -> Path:
    return root / "runtime" / QUARANTINE_FILE


def is_quarantined(root: Path, tag: str) -> bool:
    """Never retry an already-failed release until a newer tag is published.

    Corrupt or replaced state must fail closed, not silently retry an update.
    """
    path = _quarantine_path(root)
    if path.is_symlink():
        raise ValueError("unsafe quarantine file")
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("invalid quarantine state")
    stored_tag = data.get("tag")
    if not isinstance(stored_tag, str) or not VERSION_RE.fullmatch(stored_tag):
        raise ValueError("invalid quarantined tag")
    return stored_tag == tag


def quarantine_failed_activation(root: Path, tag: str, commit: str, reason: str) -> None:
    """Persist a failed activation atomically, without logs or secret material."""
    path = _quarantine_path(root)
    if path.is_symlink():
        raise ValueError("unsafe quarantine file")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "schema": 1, "tag": tag, "commit": commit,
        "failed_at": datetime.now(UTC).isoformat(), "reason": reason,
    }
    fd, name = tempfile.mkstemp(prefix=".update-quarantine-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)



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
    request = urllib.request.Request(  # noqa: S310 -- fixed GitHub HTTPS URL
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
           deploy=None, check_only: bool = False, manual: bool = False) -> dict[str, str]:
    """Validate a stable release against the exact remote before activation."""
    if not check_only and os.environ.get("OPENCODE_AUTO_UPDATE", "1") != "1":
        return {"status": "skipped", "reason": "automatic updates disabled by environment"}
    try:
        mode = deployment_mode(root)
        if mode == "off" and not (check_only or manual):
            return {"status": "skipped", "reason": "automatic updates disabled"}
        if mode == "maintenance-window" and not (check_only or manual) and not maintenance_window_open():
            return {"status": "skipped", "reason": "outside 08:00–10:00 UTC maintenance window"}
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
        if is_quarantined(root, tag):
            return {"status": "skipped", "reason": "failed release is quarantined", "tag": tag}
        # Never trust a tag name from an arbitrary repository or branch.
        git_run("fetch", "--no-tags", "origin", f"refs/tags/{tag}:refs/tags/{tag}", cwd=root)
        commit = git_run("rev-parse", f"refs/tags/{tag}^{{commit}}", cwd=root)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("bad tag commit")
        actual = git_run("show", f"{commit}:VERSION", cwd=root).strip()
        if actual != tag.removeprefix("v"):
            return {"status": "skipped", "reason": "release tag and VERSION disagree"}
        if not verified_ci(commit, tag, api=api):
            return {"status": "skipped", "reason": "tagged CI not successful yet"}
        if check_only or (mode == "notify" and not manual):
            return {"status": "available", "tag": tag, "commit": commit}
        command = [str(root / "scripts" / "deploy.sh"), tag]
        try:
            if deploy is None:
                subprocess.run(command, cwd=root, check=True, timeout=240,
                               env={**os.environ, "OPENCODE_EXPECTED_RELEASE_COMMIT": commit},
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            else:
                deploy(command)
        except Exception as exc:
            # A failed activation must never be attempted in a daily outage loop.
            # A newer stable release is eligible without manual intervention.
            quarantine_failed_activation(root, tag, commit, type(exc).__name__)
            return {"status": "error", "reason": "activation failed; release quarantined", "tag": tag}
        return {"status": "updated", "tag": tag, "commit": commit}
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            urllib.error.URLError, KeyError) as exc:
        # Do not include subprocess stderr or full URLs containing credentials.
        return {"status": "error", "reason": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="Read-only verification; never deploy")
    actions.add_argument("--apply", action="store_true", help="Explicit owner-requested update")
    args = parser.parse_args()
    result = update(check_only=args.check, manual=args.apply)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"updated", "up_to_date", "skipped", "available"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
