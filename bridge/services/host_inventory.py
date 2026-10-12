"""Read-only, secret-free inventory of a Bridge host (T01).

No network access, root commands, package upgrades or user-supplied shell text.
Only explicitly allowlisted unit names and policy files are inspected.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

RELEASE_SHA = re.compile(r"[0-9a-f]{40}\Z")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
MODES = frozenset({"off", "notify", "auto-safe", "maintenance-window"})
SERVICES = (
    ("opencode-serve.service", True),
    ("opencode-bridge-telegram.service", True),
    ("opencode-bridge-maintenance.timer", False),
)
STATES = frozenset({
    "active", "inactive", "activating", "deactivating", "failed", "reloading",
})


def _run(argv: list[str], *, directory: Path) -> tuple[int, str]:
    """Capture bounded, read-only output without displaying stderr or credentials."""
    try:
        result = subprocess.run(
            argv, cwd=directory, capture_output=True, text=True, timeout=8,
            check=False, env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
                              "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return result.returncode, result.stdout.strip()[:128]


def _read_safe(path: Path, *, max_bytes: int = 2048) -> str | None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        return None
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _policy(runtime: Path, key: str) -> str:
    """Only literal owner-selected flags are accepted as authorization."""
    if runtime.is_symlink():
        return "invalid"
    path = runtime / key
    if path.is_symlink():
        return "invalid"
    if not path.exists():
        return "unset"
    value = _read_safe(path, max_bytes=32)
    if key == "autopilot-mode":
        return value if value in MODES else "invalid"
    return {"0": "off", "1": "on"}.get(value, "invalid")


def _memory_kib() -> dict[str, int | None]:
    wanted = {"MemTotal": "total_bytes", "MemAvailable": "available_bytes",
              "SwapTotal": "swap_total_bytes", "SwapFree": "swap_free_bytes"}
    result: dict[str, int | None] = dict.fromkeys(wanted.values())
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            label, separator, value = line.partition(":")
            if separator and label in wanted:
                parts = value.strip().split()
                if len(parts) == 2 and parts[1] == "kB":
                    result[wanted[label]] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return result


def _deployment_identity(runtime: Path) -> dict[str, str]:
    if runtime.is_symlink():
        return {"status": "invalid"}
    path = runtime / "deployment-identity"
    raw = _read_safe(path, max_bytes=4096)
    if raw is None:
        return {"status": "unavailable" if not path.exists() else "invalid"}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"status": "invalid"}
    if not isinstance(value, dict):
        return {"status": "invalid"}
    tag, sha = value.get("tag"), value.get("commit")
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        return {"status": "invalid"}
    if not isinstance(sha, str) or not RELEASE_SHA.fullmatch(sha):
        return {"status": "invalid"}
    return {"status": "recorded", "tag": tag, "commit": sha}


def _dependencies() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for package in ("python-telegram-bot", "httpx", "aiosqlite"):
        try:
            value = version(package)
        except PackageNotFoundError:
            value = None
        # Never expose unexpected arbitrary metadata strings in diagnostics.
        result[package] = value if value is not None and SEMVER.fullmatch(value) else None
    return result


def _file_metadata(path: Path) -> dict[str, bool]:
    if path.is_symlink():
        return {"present": False, "safe_permissions": False}
    if not path.is_file():
        return {"present": False, "safe_permissions": False}
    try:
        permissions_ok = os.name != "posix" or path.stat().st_mode & 0o077 == 0
    except OSError:
        return {"present": False, "safe_permissions": False}
    return {"present": True, "safe_permissions": permissions_ok}


def collect_inventory(project: Path) -> dict:
    """Inventory state only; NEVER read .env, provider keys or user task content."""
    project = Path(project).resolve()
    runtime = project / "runtime"
    def command(*parts: str) -> tuple[int, str]:
        return _run(list(parts), directory=project)

    code, sha = command("git", "rev-parse", "--verify", "HEAD")
    revision = sha if code == 0 and RELEASE_SHA.fullmatch(sha) else None
    code, version_text = command("git", "show", "HEAD:VERSION")
    tracked_version = version_text if code == 0 and SEMVER.fullmatch(version_text) else None
    code, _ = command("git", "diff", "--quiet", "--no-ext-diff")
    worktree_dirty = code == 1 if code in {0, 1} else None
    code, _ = command("git", "diff", "--cached", "--quiet", "--no-ext-diff")
    index_dirty = code == 1 if code in {0, 1} else None

    services = {}
    for name, user in SERVICES:
        args = ["systemctl"]
        if user:
            args.append("--user")
        code, state = command(*args, "is-active", name)
        services[name] = state if state in STATES and code in {0, 3} else "unavailable"

    try:
        usage = shutil.disk_usage(project)
        disk = {"total_bytes": usage.total, "used_bytes": usage.used,
                "free_bytes": usage.free}
    except OSError:
        disk = {"total_bytes": None, "used_bytes": None, "free_bytes": None}

    return {
        "schema": 1,
        "collected_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": {"head_commit": revision, "head_version": tracked_version,
                "worktree_dirty": worktree_dirty, "index_dirty": index_dirty},
        "deployed": _deployment_identity(runtime),
        "services": services,
        "update_policy": {
            "bridge_mode": _policy(runtime, "autopilot-mode"),
            "legacy_bridge": _policy(runtime, "auto-update-enabled"),
            "host_os": _policy(runtime, "host-auto-update-enabled"),
        },
        "storage": {
            "database_present": (project / "sessions.db").is_file()
            and not (project / "sessions.db").is_symlink(),
            "env": _file_metadata(project / ".env"),
            "venv_present": (project / "venv" / "bin" / "python").is_file(),
            "disk": disk,
        },
        "memory": _memory_kib(),
        "runtime_dependencies": _dependencies(),
        "running_as_root": bool(hasattr(os, "geteuid") and os.geteuid() == 0),
    }
