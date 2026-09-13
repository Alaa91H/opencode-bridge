"""Lightweight host resource sampling and adaptive worker policy.

This module reads Linux procfs/statvfs only. It performs no privileged actions and
is safe to use continuously on small VPS hosts.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

_MIB = 1024 * 1024


@dataclass(frozen=True)
class ResourceSnapshot:
    total_memory_mib: int
    available_memory_mib: int
    swap_total_mib: int
    swap_free_mib: int
    cpu_count: int
    load1: float
    disk_free_percent: float
    memory_psi_avg10: float | None

    @property
    def memory_available_percent(self) -> float:
        if self.total_memory_mib <= 0:
            return 0.0
        return self.available_memory_mib * 100.0 / self.total_memory_mib

    @property
    def swap_used_mib(self) -> int:
        return max(0, self.swap_total_mib - self.swap_free_mib)

    @property
    def swap_used_percent(self) -> float:
        if self.swap_total_mib <= 0:
            return 0.0
        return self.swap_used_mib * 100.0 / self.swap_total_mib


@dataclass(frozen=True)
class WorkerDecision:
    allowed_workers: int
    configured_workers: int
    pressure: str
    reason: str
    snapshot: ResourceSnapshot


class HostResourcePolicy:
    """Compute a conservative live worker limit from host pressure signals."""

    def __init__(self, cache_seconds: float = 5.0, root_path: Path = Path("/")) -> None:
        self.cache_seconds = max(0.5, float(cache_seconds))
        self.root_path = root_path
        self._cached_at = 0.0
        self._cached_snapshot: ResourceSnapshot | None = None

    def snapshot(self, force: bool = False) -> ResourceSnapshot:
        now = time.monotonic()
        if not force and self._cached_snapshot is not None and now - self._cached_at < self.cache_seconds:
            return self._cached_snapshot

        mem = self._read_meminfo()
        load1 = self._read_load1()
        cpu_count = max(1, os.cpu_count() or 1)
        stat = os.statvfs(self.root_path)
        disk_total = stat.f_blocks * stat.f_frsize
        disk_free = stat.f_bavail * stat.f_frsize
        disk_free_percent = (disk_free * 100.0 / disk_total) if disk_total else 0.0

        snapshot = ResourceSnapshot(
            total_memory_mib=mem.get("MemTotal", 0) // 1024,
            available_memory_mib=mem.get("MemAvailable", mem.get("MemFree", 0)) // 1024,
            swap_total_mib=mem.get("SwapTotal", 0) // 1024,
            swap_free_mib=mem.get("SwapFree", 0) // 1024,
            cpu_count=cpu_count,
            load1=load1,
            disk_free_percent=disk_free_percent,
            memory_psi_avg10=self._read_memory_psi(),
        )
        self._cached_snapshot = snapshot
        self._cached_at = now
        return snapshot

    def decide(self, configured_workers: int) -> WorkerDecision:
        configured = max(1, min(int(configured_workers), 8))
        snap = self.snapshot()
        allowed = configured
        pressure = "normal"
        reasons: list[str] = []

        # Memory is the strongest limiter on small VPS hosts because concurrent
        # LLM/tool sessions can spike Python, Git, and OpenCode memory at once.
        if snap.available_memory_mib < 384 or snap.memory_available_percent < 12:
            allowed = 1
            pressure = "critical"
            reasons.append("low available memory")
        elif snap.available_memory_mib < 768 or snap.memory_available_percent < 22:
            allowed = min(allowed, 2)
            pressure = "high"
            reasons.append("memory pressure")

        if snap.memory_psi_avg10 is not None:
            if snap.memory_psi_avg10 >= 10.0:
                allowed = 1
                pressure = "critical"
                reasons.append("sustained memory stalls")
            elif snap.memory_psi_avg10 >= 2.0:
                allowed = min(allowed, 2)
                if pressure == "normal":
                    pressure = "high"
                reasons.append("memory stalls")

        # Sustained swap occupancy is a strong sign that the host is carrying
        # working sets larger than RAM. Keep this conservative because another
        # concurrent coding session can turn reclaim pressure into an OOM event.
        if snap.swap_total_mib >= 256:
            if snap.swap_used_percent >= 90.0:
                allowed = 1
                pressure = "critical"
                reasons.append("swap nearly exhausted")
            elif snap.swap_used_percent >= 70.0:
                allowed = min(allowed, 2)
                if pressure == "normal":
                    pressure = "high"
                reasons.append("high swap usage")

        normalized_load = snap.load1 / max(1, snap.cpu_count)
        if normalized_load >= 2.0:
            allowed = 1
            pressure = "critical"
            reasons.append("CPU saturation")
        elif normalized_load >= 1.2:
            allowed = min(allowed, max(1, snap.cpu_count))
            if pressure == "normal":
                pressure = "high"
            reasons.append("high CPU load")

        if snap.disk_free_percent < 5.0:
            allowed = 1
            pressure = "critical"
            reasons.append("critical disk space")
        elif snap.disk_free_percent < 10.0:
            allowed = min(allowed, 2)
            if pressure == "normal":
                pressure = "high"
            reasons.append("low disk space")

        # Avoid aggressive concurrency on tiny hosts even when they are idle.
        if snap.total_memory_mib and snap.total_memory_mib < 1536:
            allowed = min(allowed, 1)
            reasons.append("small-memory host")
        elif snap.total_memory_mib and snap.total_memory_mib < 3072:
            allowed = min(allowed, 2)
            reasons.append("memory-sized concurrency cap")

        reason = ", ".join(dict.fromkeys(reasons)) if reasons else "resources healthy"
        return WorkerDecision(
            allowed_workers=max(1, allowed),
            configured_workers=configured,
            pressure=pressure,
            reason=reason,
            snapshot=snap,
        )

    @staticmethod
    def _read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
        result: dict[str, int] = {}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if not separator:
                    continue
                number = value.strip().split(maxsplit=1)[0]
                if number.isdigit():
                    result[key] = int(number)
        except OSError:
            pass
        return result

    @staticmethod
    def _read_load1(path: Path = Path("/proc/loadavg")) -> float:
        try:
            return float(path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return 0.0

    @staticmethod
    def _read_memory_psi(path: Path = Path("/proc/pressure/memory")) -> float | None:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.startswith("some "):
                    continue
                for field in line.split()[1:]:
                    if field.startswith("avg10="):
                        return float(field.split("=", 1)[1])
        except (OSError, ValueError):
            return None
        return None


def format_decision(decision: WorkerDecision) -> str:
    snap = decision.snapshot
    psi = "n/a" if snap.memory_psi_avg10 is None else f"{snap.memory_psi_avg10:.2f}%"
    return (
        f"Pressure: {decision.pressure}\n"
        f"Workers: {decision.allowed_workers}/{decision.configured_workers}\n"
        f"Memory: {snap.available_memory_mib} MiB available / {snap.total_memory_mib} MiB total "
        f"({snap.memory_available_percent:.1f}% available)\n"
        f"Swap: {snap.swap_used_mib} MiB used / {snap.swap_total_mib} MiB total "
        f"({snap.swap_used_percent:.1f}% used)\n"
        f"CPU: load1 {snap.load1:.2f} across {snap.cpu_count} CPU(s)\n"
        f"Disk free: {snap.disk_free_percent:.1f}%\n"
        f"Memory PSI avg10: {psi}\n"
        f"Policy: {decision.reason}"
    )
