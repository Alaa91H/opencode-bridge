"""Hand the outcome of a detached Bridge update back to the owner.

A Bridge update restarts the very service that asked for it, so the process that
started the update never survives to report the result. The release updater
therefore leaves ``runtime/update-result.json`` behind, and the *new* bot process
reads it on start-up. The same hook resolves an ``applying`` ledger entry that a
killed updater could not finish, so one interrupted update can never block every
later release.

No subprocesses and no secret values live here.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from bridge.services.update_coordinator import UpdateCoordinator, write_quarantine

RESULT_FILE = "update-result.json"
REPORTED_FILE = "update-result.reported.json"
_MAX_AGE_SECONDS = 6 * 3600
_STATUSES = frozenset({"updated", "error", "skipped", "up_to_date", "available"})
_TAG = re.compile(r"v[0-9]+[.][0-9]+[.][0-9]+\Z")
_REASON = re.compile(r"[^\x20-\x7e]")


def result_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / RESULT_FILE


def write_result(runtime_dir: Path, *, status: str, trigger: str, tag: str | None = None,
                 commit: str | None = None, reason: str | None = None) -> None:
    """Atomically record a finished update attempt (private file, no logs)."""
    import tempfile
    from datetime import UTC, datetime

    if status not in _STATUSES:
        raise ValueError("invalid update status")
    target = result_path(runtime_dir)
    if target.is_symlink():
        raise ValueError("unsafe update result file")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "schema": 1, "status": status, "trigger": trigger[:16],
        "tag": tag if isinstance(tag, str) and _TAG.fullmatch(tag) else None,
        "commit": commit[:40] if isinstance(commit, str) else None,
        "reason": _REASON.sub("?", reason)[:120] if reason else None,
        "finished_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    fd, name = tempfile.mkstemp(prefix=".update-result-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def read_result(runtime_dir: Path, *, max_age: float = _MAX_AGE_SECONDS,
                since: float | None = None) -> dict | None:
    """Return a fresh, well-formed result or ``None`` (corrupt data is ignored).

    ``since`` (epoch seconds) additionally rejects results written before an update began.
    """
    path = result_path(runtime_dir)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        modified = path.stat().st_mtime
        if time.time() - modified > max_age or (since is not None and modified < since - 1):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("schema") != 1
            or value.get("status") not in _STATUSES):
        return None
    return value


def consume_result(runtime_dir: Path) -> None:
    """Mark the current result as delivered so it is never announced twice."""
    path = result_path(runtime_dir)
    try:
        if path.is_file() and not path.is_symlink():
            os.replace(path, path.with_name(REPORTED_FILE))
    except OSError:
        pass


def describe(result: dict) -> str:
    tag = result.get("tag") or "?"
    status = result["status"]
    if status == "updated":
        return f"✅ تم تحديث Bridge إلى {tag}\nBridge updated to {tag}"
    if status == "error":
        reason = result.get("reason") or "unknown"
        return (f"⚠️ فشل تحديث Bridge ({tag}): {reason}. تم التراجع أو الحجر؛ راجع قسم التحديث.\n"
                f"Bridge update failed ({tag}): {reason}. Rolled back or quarantined.")
    if status == "available":
        return f"🔎 يتوفر إصدار {tag} جاهز للتحديث\nRelease {tag} is available"
    if status == "up_to_date":
        return "✅ Bridge محدّث لآخر إصدار\nBridge is up to date"
    return f"ℹ️ لم يُنفَّذ التحديث: {result.get('reason') or status}\nUpdate skipped: {result.get('reason') or status}"


def _deployed_ref(runtime_dir: Path) -> str | None:
    path = Path(runtime_dir) / "deployed-ref"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text(encoding="ascii").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


async def announce_update_outcome(send: Callable[[str], Awaitable[None]],
                                  runtime_dir: Path) -> str | None:
    """Resolve an interrupted update and tell the owner how the last one ended.

    Returns the message that was sent (for tests/logging), or ``None``. Never raises:
    start-up must not depend on the success of a status notification.
    """
    runtime_dir = Path(runtime_dir)
    reconciled = None
    try:
        coordinator = UpdateCoordinator(runtime_dir)
        reconciled = coordinator.reconcile_interrupted(_deployed_ref(runtime_dir))
        if reconciled is not None and reconciled.phase == "quarantined":
            write_quarantine(runtime_dir, reconciled.tag, reconciled.commit,
                             "interrupted before the deployment was confirmed")
    except Exception:
        reconciled = None

    message: str | None = None
    result = read_result(runtime_dir)
    if result is not None:
        message = describe(result)
    elif reconciled is not None:
        if reconciled.phase == "healthy":
            message = (f"✅ اكتمل تحديث Bridge إلى {reconciled.tag}\n"
                       f"Bridge update to {reconciled.tag} completed")
        else:
            message = (f"⚠️ انقطع تحديث Bridge {reconciled.tag} قبل تأكيده؛ وُضع في الحجر ولن يُعاد تلقائيًا.\n"
                       f"Bridge update {reconciled.tag} was interrupted and is quarantined.")
    if message is None:
        return None
    try:
        await send(message)
    except Exception:
        return None
    consume_result(runtime_dir)
    return message
