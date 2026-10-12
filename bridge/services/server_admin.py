"""Read-only host health and fixed, owner-approved update commands."""
from __future__ import annotations

import asyncio
import os
import shutil
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
    if kind not in {"agent", "system"}:
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
        if kind == "agent":
            old_version = await run(["opencode", "--version"], 30)
            result, log = await run(["opencode", "upgrade"], 600)
            if result:
                return f"OpenCode upgrade failed ({result})\n{log}"
            new_version = await run(["opencode", "--version"], 30)
            service, status_log = await run(
                ["systemctl", "--user", "restart", "opencode-serve.service"], 40)
            return (f"OpenCode upgrade completed.\nBefore: {old_version[1]}\n"
                    f"After: {new_version[1]}\nService restart={service}\n"
                    + status_log[-500:] + "\n" + log[-1200:])
        code, log = await run(["sudo", "-n", "/usr/bin/apt-get", "-y", "update"], 400)
        if code:
            return f"APT index update failed ({code})\n{log}"
        code, upgraded = await run(
            ["sudo", "-n", "/usr/bin/apt-get", "-y", "upgrade"], 1800)
        return f"Operating system update exit={code}\n" + (log + "\n" + upgraded)[-3000:]
    except (OSError, TimeoutError) as exc:
        return f"{kind}: {type(exc).__name__}; update could not be verified"
