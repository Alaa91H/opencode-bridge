"""Owner-approved host controls: fixed argv, bounded output, no shell.

Safety model
------------
* Every command is assembled from constants in this module. Caller input is only
  ever a *key* that is looked up in an allow-list; it is never interpolated into
  an argument vector, a path or a shell string.
* Everything runs as the unprivileged service user. The single optional
  escalation is ``sudo -n /usr/bin/systemctl reboot``, which works only if the
  host administrator separately installed ``opencode-bridge-power.sudoers``.
* Output is size-bounded while it is read and redacted before it can reach
  Telegram.
* Actions that would stop the bot itself (restarting the Bridge unit or the
  whole stack, or an update that restarts the Bridge) are launched as a
  *transient systemd user unit*. That unit lives in its own cgroup, so the
  restart it performs cannot kill the process that is carrying it out.
* Destructive actions share one guard with the update path, so a restart or a
  reboot cannot land in the middle of an upgrade.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from bridge.services.host_operations import HOST_OPERATIONS

__all__ = [
    "SERVICES",
    "ActionResult",
    "CommandResult",
    "DetachedUnavailable",
    "HostControl",
    "HostControlError",
    "launch_transient",
    "redact",
    "run_fixed",
]

# key -> (systemd user unit, does restarting it terminate this bot process?)
SERVICES: dict[str, tuple[str, bool]] = {
    "opencode": ("opencode-serve.service", False),
    "bridge": ("opencode-bridge-telegram.service", True),
    "stack": ("opencode-bridge.target", True),
}
LOG_SERVICES = ("opencode", "bridge")

_MAX_CAPTURE_BYTES = 256 * 1024
_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
_SYSTEMCTL = "/usr/bin/systemctl"
_REBOOT_ARGV = ("sudo", "-n", _SYSTEMCTL, "reboot")


class HostControlError(RuntimeError):
    """A bounded, user-presentable failure (never carries secret material)."""


class DetachedUnavailable(HostControlError):
    """A transient systemd user unit could not be created."""


@dataclass(frozen=True)
class CommandResult:
    code: int
    output: str


@dataclass(frozen=True)
class ActionResult:
    """Outcome code for the UI to localise; ``detail`` is already redacted."""

    ok: bool
    code: str
    detail: str = ""


_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<redacted-telegram-token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "<redacted-token>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "<redacted-token>"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "<redacted-key>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
    # [\w-]* lets env-style names such as OPENCODE_SERVER_PASSWORD match: "_" is a word
    # character, so a plain \bpassword\b would miss the most common leak format.
    (re.compile(
        r"(?i)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key|authorization))\b"
        r"(\s*[=:]\s*)(?!<redacted>)\S+"),
     r"\1\2<redacted>"),
)


def redact(text: str) -> str:
    """Mask credentials that commonly appear in service logs and command output."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _clean_text(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").replace("\x00", "")
    # Strip terminal control sequences so output cannot corrupt a Telegram message.
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)


async def run_fixed(argv: list[str] | tuple[str, ...], *, timeout: float,
                    max_bytes: int = _MAX_CAPTURE_BYTES) -> CommandResult:
    """Run one fixed command without a shell, bounding time and captured bytes."""
    if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
        raise HostControlError("invalid command")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        raise HostControlError(f"cannot start {os.path.basename(argv[0])}: {type(exc).__name__}") from exc

    async def drain() -> bytes:
        if process.stdout is None:
            return b""
        chunks: list[bytes] = []
        size = 0
        killed = False
        while True:
            block = await process.stdout.read(8192)
            if not block:
                break
            if killed:
                continue  # Discard: asyncio finishes wait() only once the pipe reaches EOF.
            size += len(block)
            if size > max_bytes:
                chunks.append(block[: max(0, max_bytes - (size - len(block)))])
                process.kill()
                killed = True
                continue
            chunks.append(block)
        return b"".join(chunks)

    try:
        raw = await asyncio.wait_for(drain(), timeout)
        code = await asyncio.wait_for(process.wait(), 10)
    except TimeoutError as exc:
        raise HostControlError(f"{os.path.basename(argv[0])} timed out") from exc
    finally:
        if process.returncode is None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                pass  # The kernel has the SIGKILL; never let cleanup itself hang the bot.
    return CommandResult(code, _clean_text(raw))


_ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


async def launch_transient(argv: list[str], *, name: str, delay_seconds: int = 0,
                           working_directory: Path | None = None,
                           environment: dict[str, str] | None = None) -> str:
    """Start ``argv`` in its own systemd user unit and return the unit name.

    The unit is created in a separate cgroup, so restarting the Bridge service
    (which kills every process in *its* cgroup) cannot terminate the command.
    Fails closed with :class:`DetachedUnavailable` instead of falling back to a
    child process that would be killed half-way through.
    """
    if not _NAME_RE.fullmatch(name):
        raise ValueError("invalid transient unit name")
    runner = shutil.which("systemd-run")
    if runner is None:
        raise DetachedUnavailable("systemd-run is not available on this host")
    unit = f"{name}-{int(time.time())}"
    command = [runner, "--user", "--quiet", "--collect", f"--unit={unit}"]
    if delay_seconds > 0:
        command.append(f"--on-active={int(delay_seconds)}s")
    if working_directory is not None:
        command.append(f"--working-directory={working_directory}")
    for key, value in (environment or {}).items():
        # A transient unit does not inherit the Bridge service environment, so
        # only explicitly named, non-secret settings are passed through.
        if not _ENV_KEY_RE.fullmatch(key) or "\n" in value or "\x00" in value:
            raise ValueError("invalid transient unit environment")
        command.append(f"--setenv={key}={value}")
    command += ["--", *argv]
    result = await run_fixed(command, timeout=20)
    if result.code != 0:
        raise DetachedUnavailable("systemd-run refused to create the transient unit")
    return unit


def _read_processes(proc_root: Path) -> dict[int, tuple[str, int, int]]:
    """pid -> (comm, resident bytes, cpu ticks). Only /proc stat files are read."""
    page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    found: dict[int, tuple[str, int, int]] = {}
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (Path(entry.path) / "stat").read_text(encoding="utf-8", errors="replace")
            left, right = stat.find("("), stat.rfind(")")
            if left < 0 or right < left:
                continue
            fields = stat[right + 2:].split()
            # fields[0] is overall field 3 (state): utime=14, stime=15, rss=24.
            ticks = int(fields[11]) + int(fields[12])
            rss = int(fields[21]) * page
            found[int(entry.name)] = (stat[left + 1:right][:32], rss, ticks)
        except (OSError, ValueError, IndexError):
            continue
    return found


def _tree_size(root: Path, budget: int) -> tuple[int, bool]:
    """Disk usage of a tree without following links; stops after ``budget`` entries."""
    total, seen = 0, 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as scan:
                for entry in scan:
                    seen += 1
                    if seen > budget:
                        return total, True
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, False


def _busy() -> ActionResult | None:
    """Refuse a destructive action while another host operation owns the host.

    A reboot or service restart in the middle of an upgrade is the failure that
    leaves a host half-updated, so it is refused rather than deferred.
    """
    if not HOST_OPERATIONS.busy:
        return None
    return ActionResult(False, "busy", HOST_OPERATIONS.current or "")


class HostControl:
    """Operational controls for the owner-only Telegram panel."""

    def __init__(self, project: Path, *, proc_root: Path = Path("/proc"),
                 backup_root: Path | None = None) -> None:
        self.project = Path(project)
        self.proc_root = Path(proc_root)
        self.backup_root = backup_root if backup_root is not None else (
            Path.home() / "opencode-backups")

    # ------------------------------------------------------------ services
    async def restart_service(self, key: str) -> ActionResult:
        if key not in SERVICES:
            raise ValueError("unknown service key")
        blocked = _busy()
        if blocked is not None:
            return blocked
        unit, stops_bot = SERVICES[key]
        argv = [_SYSTEMCTL, "--user", "restart", unit]
        if stops_bot:
            try:
                await launch_transient(argv, name=f"opencode-bridge-restart-{key}", delay_seconds=3)
            except DetachedUnavailable as exc:
                return ActionResult(False, "unavailable", str(exc))
            return ActionResult(True, "scheduled")
        try:
            result = await run_fixed(argv, timeout=90)
            if result.code != 0:
                return ActionResult(False, "failed", redact(result.output)[-300:])
            for _ in range(10):
                state = await run_fixed([_SYSTEMCTL, "--user", "is-active", unit], timeout=15)
                if state.output.strip() == "active":
                    return ActionResult(True, "restarted")
                await asyncio.sleep(1)
        except HostControlError as exc:
            return ActionResult(False, "failed", str(exc))
        return ActionResult(False, "not_active")

    async def service_logs(self, key: str, lines: int = 40) -> str:
        if key not in LOG_SERVICES:
            raise ValueError("unknown log source")
        unit = SERVICES[key][0]
        count = max(5, min(int(lines), 80))
        result = await run_fixed(
            ["journalctl", "--user", "-u", unit, "-n", str(count), "--no-pager", "-o", "short-iso"],
            timeout=20)
        text = redact(result.output).strip()
        if result.code != 0 and not text:
            raise HostControlError("journal is not readable by the service user")
        return text[-3000:]

    async def failed_units(self) -> list[str]:
        names: list[str] = []
        for scope in (["--user"], []):
            try:
                result = await run_fixed(
                    [_SYSTEMCTL, *scope, "--failed", "--no-legend", "--plain", "--no-pager"], timeout=15)
            except HostControlError:
                continue
            for line in result.output.splitlines():
                token = line.split(None, 1)[0] if line.strip() else ""
                if re.fullmatch(r"[A-Za-z0-9@_.:\\-]{1,80}", token):
                    names.append(("user:" if scope else "system:") + token)
        return names[:12]

    # ------------------------------------------------------- consumption
    async def top_processes(self, limit: int = 8, interval: float = 0.5) -> list[dict]:
        """Top resident-memory processes with CPU% measured over ``interval``."""
        first = await asyncio.to_thread(_read_processes, self.proc_root)
        await asyncio.sleep(interval)
        second = await asyncio.to_thread(_read_processes, self.proc_root)
        ticks_per_second = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        rows = []
        for pid, (comm, rss, ticks) in second.items():
            before = first.get(pid)
            cpu = 0.0
            if before is not None and interval > 0:
                cpu = max(0.0, (ticks - before[2]) / ticks_per_second / interval * 100)
            rows.append({"pid": pid, "name": comm, "rss": rss, "cpu": round(cpu, 1)})
        rows.sort(key=lambda row: (row["rss"], row["cpu"]), reverse=True)
        return rows[: max(1, min(limit, 15))]

    async def disk_breakdown(self, *, entry_budget: int = 200_000) -> dict:
        runtime = self.project / "runtime"
        candidates: list[tuple[str, Path]] = []
        if runtime.is_dir() and not runtime.is_symlink():
            candidates.extend((f"runtime/{child.name}", child) for child in sorted(runtime.iterdir()))
        candidates.extend((name, self.project / name) for name in (
            "sessions.db", "sessions.db-wal", "sessions.db-shm", ".git", "venv"))
        if self.backup_root.is_dir():
            candidates.append(("backups", self.backup_root))

        def measure() -> list[tuple[str, int, bool]]:
            remaining = entry_budget
            rows: list[tuple[str, int, bool]] = []
            for label, path in candidates:
                if path.is_symlink() or not path.exists():
                    continue
                if path.is_dir():
                    size, truncated = _tree_size(path, remaining)
                else:
                    size, truncated = path.stat().st_size, False
                remaining = max(0, remaining - 1)
                rows.append((label, size, truncated))
            rows.sort(key=lambda row: row[1], reverse=True)
            return rows[:10]

        rows = await asyncio.to_thread(measure)
        usage = shutil.disk_usage(self.project)
        return {"items": rows, "free": usage.free, "total": usage.total}

    # ---------------------------------------------------------- cleanup
    async def cleanup_now(self) -> ActionResult:
        """Expire downloads past retention and vacuum the user journal. Never touches tasks."""
        from bridge.services.cache_retention import manual_cleanup

        # Not dangerous on its own, but running it under an in-flight upgrade or
        # a scheduled reboot is: refuse while another operation owns the host.
        blocked = _busy()
        if blocked is not None:
            return blocked
        try:
            outcome = await asyncio.to_thread(manual_cleanup, self.project)
        except (OSError, ValueError) as exc:
            return ActionResult(False, "failed", type(exc).__name__)
        if outcome.get("status") != "completed":
            return ActionResult(False, "skipped", str(outcome.get("reason", "")))
        detail = ", ".join(f"{key}={value}" for key, value in sorted(outcome.items())
                           if key != "status" and isinstance(value, (int, str)))
        try:
            await run_fixed(["journalctl", "--user", "--vacuum-size=200M"], timeout=60)
        except HostControlError:
            pass  # Optional; a missing user journal must not fail the cleanup.
        return ActionResult(True, "cleaned", detail)

    # ------------------------------------------------------------ power
    async def power_available(self) -> bool:
        """True only if the admin installed the optional reboot sudoers entry."""
        try:
            result = await run_fixed(["sudo", "-n", "-l", _SYSTEMCTL, "reboot"], timeout=10)
        except HostControlError:
            return False
        return result.code == 0

    async def reboot(self) -> ActionResult:
        # Refused before the sudoers probe: checking power availability while an
        # upgrade owns the host would run a sudo command for nothing.
        blocked = _busy()
        if blocked is not None:
            return blocked
        if not await self.power_available():
            return ActionResult(False, "power_disabled")
        try:
            await launch_transient(list(_REBOOT_ARGV), name="opencode-bridge-reboot", delay_seconds=5)
        except DetachedUnavailable as exc:
            return ActionResult(False, "unavailable", str(exc))
        return ActionResult(True, "scheduled")
