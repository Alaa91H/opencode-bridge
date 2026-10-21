"""Read-only host health and fixed, owner-approved update commands."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from bridge.services.host_operations import HOST_OPERATIONS

# Non-secret update settings that a transient systemd unit would otherwise not inherit
# from the Bridge service environment (.env). OPENCODE_AUTO_UPDATE=0 stays a real kill switch.
_UPDATE_ENV_PASSTHROUGH = ("OPENCODE_AUTO_UPDATE", "OPENCODE_UPDATE_REPOSITORY",
                           "OPENCODE_UPDATE_DEPLOY_TIMEOUT")
_UPDATE_WAIT_SECONDS = 180


def resources(project: Path) -> dict:
    """Host metrics without optional dependencies or exposing process secrets."""
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, _, rest = line.partition(":")
            if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                mem[name] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    disk = shutil.disk_usage(project)
    try:
        uptime = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        uptime = 0
    return {
        "utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "cpu_load_1m": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
        "logical_cpus": os.cpu_count(),
        "uptime_seconds": uptime,
        "memory_total": mem.get("MemTotal"),
        "memory_available": mem.get("MemAvailable"),
        "swap_total": mem.get("SwapTotal"),
        "swap_free": mem.get("SwapFree"),
        "disk_total": disk.total,
        "disk_free": disk.free,
        "disk_used": disk.used,
    }


async def _apply_bridge_detached(project: Path, progress=None) -> str:
    """Run the verified release deployment outside this service's cgroup.

    ``deploy.sh`` restarts ``opencode-bridge-telegram.service``; any child of this very
    process would be killed with it before the health check, the deployment record and the
    ledger could be completed. A transient systemd unit survives that restart, and the next
    bot process reports the outcome (see ``update_report``). If systemd cannot create the
    unit the update is NOT attempted in-process: failing closed beats a half-applied release.
    """
    from bridge.services import update_report
    from bridge.services.host_control import DetachedUnavailable, launch_transient

    updater = project / "maintenance" / "self_update.py"
    environment = {key: os.environ[key] for key in _UPDATE_ENV_PASSTHROUGH if key in os.environ}
    started = time.time()
    try:
        unit = await launch_transient(
            [sys.executable, str(updater), "--apply"], name="opencode-bridge-update",
            working_directory=project, environment=environment)
    except DetachedUnavailable as exc:
        return (f"Bridge update was not started: {exc}.\n"
                "The update restarts this bot, so it must run in its own systemd unit. "
                "Nothing was changed.")
    runtime = project / "runtime"
    deadline = time.monotonic() + _UPDATE_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        result = update_report.read_result(runtime, since=started)
        if result is not None:
            update_report.consume_result(runtime)
            return update_report.describe(result)
        if progress is not None:
            try:
                await progress(f"running in background unit {unit}")
            except Exception:
                pass
    return (f"Bridge update is running in the background ({unit}). This bot may restart; "
            "the final result will be sent here automatically.")


async def update_now(kind: str, progress=None) -> str:
    """Run one host update, refusing to overlap with another destructive action.

    The guard is the point of this wrapper: a second tap on an update button used
    to start a second operation (two apt runs, two bridge deployments), and a
    reboot could land in the middle of an upgrade. Refusal is immediate and
    explained rather than queued.
    """
    if kind not in {"agent", "system", "bridge-check", "bridge"}:
        raise ValueError("Unsupported update target")
    return await HOST_OPERATIONS.run(kind, lambda: _run_update(kind, progress))


async def _run_update(kind: str, progress=None) -> str:
    if kind != "bridge-check":
        from bridge.services.runtime_controls import RuntimeControls

        project = Path(__file__).resolve().parents[2]
        try:
            mode = RuntimeControls(project / "runtime").autopilot_mode()
        except (OSError, ValueError):
            return "Update blocked: invalid owner update policy"
        if mode == "emergency-stop":
            return "Update blocked: owner emergency stop is active"

    async def run(command, timeout):
        from time import monotonic
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
        chunks = []
        deadline = monotonic() + timeout
        next_report = monotonic()
        try:
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Update command exceeded deadline")
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if not line:
                    break
                chunks.append(line.decode("utf-8", "replace").strip()[:400])
                if len(chunks) > 80:
                    chunks.pop(0)
                if progress is not None and monotonic() >= next_report:
                    next_report = monotonic() + 5
                    try:
                        await progress("\n".join(chunks[-7:])[-2000:])
                    except Exception:
                        pass
            return await process.wait(), "\n".join(chunks)[-3000:]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    try:
        if kind == "bridge":
            return await _apply_bridge_detached(Path(__file__).resolve().parents[2], progress)
        if kind == "bridge-check":
            updater = Path(__file__).resolve().parents[2] / "maintenance" / "self_update.py"
            # Read-only: verifies the exact release commit and CI, never activates it.
            code, report = await run([sys.executable, str(updater), "--check"], 90)
            return f"Bridge update check {'completed' if code == 0 else 'failed'} (exit={code}):\n" + report[-2500:]
        if kind == "agent":
            old_status, old_version = await run(["opencode", "--version"], 30)
            if old_status or not old_version.strip():
                return "OpenCode upgrade not started: current version could not be verified"
            result, log = await run(["opencode", "upgrade"], 600)
            if result:
                return f"OpenCode upgrade failed ({result})\n{log}"
            new_status, new_version = await run(["opencode", "--version"], 30)
            if new_status or not new_version.strip():
                return "OpenCode upgrade applied, but the installed version could not be verified"
            service, status_log = await run(
                ["systemctl", "--user", "restart", "opencode-serve.service"], 40)
            if service:
                return (f"OpenCode upgrade applied, but service restart failed ({service}).\n"
                        + status_log[-500:])
            health_code, health_status = await run(
                ["systemctl", "--user", "is-active", "opencode-serve.service"], 20)
            if health_code or health_status.strip() != "active":
                return (f"OpenCode upgrade applied, but the service did not become active "
                        f"(exit={health_code}, state={health_status[-80:]}). "
                        "Recovery is required.")
            return f"OpenCode upgrade verified (service active).\nBefore: {old_version}\nAfter: {new_version}\n" + log[-1200:]
        code, log = await run(["sudo", "-n", "/usr/bin/apt-get", "-y", "update"], 400)
        if code:
            return f"APT index update failed ({code})\n{log}"
        code, upgraded = await run(
            ["sudo", "-n", "/usr/bin/apt-get", "-y", "upgrade"], 1800)
        return f"Operating system update exit={code}\n" + (log + "\n" + upgraded)[-3000:]
    except (OSError, TimeoutError) as exc:
        return f"{kind}: {type(exc).__name__}; update could not be verified"
