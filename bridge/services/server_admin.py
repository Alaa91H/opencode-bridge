"""Read-only host health and fixed, owner-approved update commands."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path


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


async def update_now(kind: str, progress=None) -> str:
    """Stream bounded operational status; only known non-build commands."""
    if kind not in {"agent", "system", "bridge-check", "bridge"}:
        raise ValueError("Unsupported update target")

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
        if kind in {"bridge", "bridge-check"}:
            updater = Path(__file__).resolve().parents[2] / "maintenance" / "self_update.py"
            # Both paths verify the exact release commit and CI. The check-only
            # command can never activate it; --apply requires a confirmed owner action.
            action = "--check" if kind == "bridge-check" else "--apply"
            code, report = await run([sys.executable, str(updater), action],
                                     90 if kind == "bridge-check" else 300)
            label = "Bridge update check" if kind == "bridge-check" else "Bridge update"
            return f"{label} {'completed' if code == 0 else 'failed'} (exit={code}):\n" + report[-2500:]
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
            return (f"OpenCode upgrade verified (service active).\nBefore: {old_version}\n"
                    f"After: {new_version}\n" + log[-1200:])
        code, log = await run(["sudo", "-n", "/usr/bin/apt-get", "-y", "update"], 400)
        if code:
            return f"APT index update failed ({code})\n{log}"
        code, upgraded = await run(
            ["sudo", "-n", "/usr/bin/apt-get", "-y", "upgrade"], 1800)
        return f"Operating system update exit={code}\n" + (log + "\n" + upgraded)[-3000:]
    except (OSError, TimeoutError) as exc:
        return f"{kind}: {type(exc).__name__}; update could not be verified"
