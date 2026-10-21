"""Read-only systemd service overview for the owner-only Telegram panel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

ALLOWED_UNITS = (
    ("opencode-serve.service", True),
    ("opencode-bridge-telegram.service", True),
    ("opencode-bridge-maintenance.timer", False),
)
_KNOWN_STATES = frozenset({
    "active", "inactive", "failed", "activating", "deactivating", "reloading",
})


@dataclass(frozen=True)
class ServiceState:
    unit: str
    status: str


async def unit_status(unit: str, *, user: bool) -> ServiceState:
    """No shell; fixed names only; no elevated or mutating commands."""
    if (unit, user) not in ALLOWED_UNITS:
        raise ValueError("Unsupported systemd unit")
    argv = ["systemctl"]
    if user:
        argv.append("--user")
    argv.extend(("is-active", unit))
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=8)
        value = stdout.decode("ascii", "replace").strip()
        return ServiceState(unit, value if value in _KNOWN_STATES else "unavailable")
    except (OSError, TimeoutError):
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return ServiceState(unit, "unavailable")


async def service_overview() -> list[ServiceState]:
    return await asyncio.gather(*(unit_status(name, user=is_user)
                                  for name, is_user in ALLOWED_UNITS))
