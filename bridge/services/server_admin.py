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


async def update_now(kind: str) -> str:
    """Only two fixed update commands; never execute arbitrary user input."""
    if kind not in {"agent", "system"}:
        raise ValueError("Invalid update target")
    if kind == "agent":
        command = ["opencode", "upgrade"]
        timeout = 600
    else:
        # Requires a narrowly-scoped sudoers rule installed separately by root.
        command = ["sudo", "-n", "/usr/bin/apt-get", "-y", "update"]
        timeout = 300
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (OSError, TimeoutError) as exc:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        return f"{kind}: {type(exc).__name__} (update not confirmed)"
    # Never include unbounded command output in Telegram.
    report = output.decode("utf-8", "replace")[-2800:]
    if kind == "system" and process.returncode == 0:
        try:
            upgrade = await asyncio.create_subprocess_exec(
                "sudo", "-n", "/usr/bin/apt-get", "-y", "upgrade",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
            )
            extra, _ = await asyncio.wait_for(upgrade.communicate(), timeout=1500)
            return f"System update exit={upgrade.returncode}\n" + (report + "\n" + extra.decode("utf-8", "replace"))[-3000:]
        except (OSError, TimeoutError):
            return "System upgrade could not complete; check elevated permissions"
    return f"{kind} update exit={process.returncode}\n{report}"
