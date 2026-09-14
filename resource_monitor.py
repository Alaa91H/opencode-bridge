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
_HEALTH_RANK = {"healthy": 0, "degraded": 1, "high": 2, "critical": 3}


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
    health_score: int = 100
    health_level: str = "healthy"
    shadow_health_level: str = "healthy"
    shadow_allowed_workers: int | None = None
    shadow_worker_delta: int = 0
    comparison_samples: int = 0
    comparison_agreements: int = 0
    comparison_disagreements: int = 0
    comparison_cumulative_abs_delta: int = 0
    comparison_max_abs_delta: int = 0


class HostResourcePolicy:
    """Compute a conservative live worker limit from host pressure signals."""

    def __init__(self, cache_seconds: float = 5.0, root_path: Path = Path("/")) -> None:
        self.cache_seconds = max(0.5, float(cache_seconds))
        self.root_path = root_path
        self._cached_at = 0.0
        self._cached_snapshot: ResourceSnapshot | None = None
        self._shadow_health_level: str | None = None
        self._comparison_samples = 0
        self._comparison_agreements = 0
        self._comparison_disagreements = 0
        self._comparison_cumulative_abs_delta = 0
        self._comparison_max_abs_delta = 0

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

    @staticmethod
    def health_score(snapshot: ResourceSnapshot) -> int:
        """Return a 0..100 current-host health score from independent pressures."""
        penalty = 0
        memory_percent = snapshot.memory_available_percent
        if snapshot.available_memory_mib < 384 or memory_percent < 12.0:
            penalty += 50
        elif snapshot.available_memory_mib < 768 or memory_percent < 22.0:
            penalty += 30
        elif memory_percent < 35.0:
            penalty += 12

        psi = snapshot.memory_psi_avg10
        if psi is not None:
            if psi >= 10.0:
                penalty += 40
            elif psi >= 2.0:
                penalty += 22
            elif psi >= 0.5:
                penalty += 8

        if snapshot.swap_total_mib >= 256:
            swap_percent = snapshot.swap_used_percent
            if swap_percent >= 90.0:
                penalty += 35
            elif swap_percent >= 70.0:
                penalty += 22
            elif swap_percent >= 40.0:
                penalty += 8

        normalized_load = snapshot.load1 / max(1, snapshot.cpu_count)
        if normalized_load >= 2.0:
            penalty += 35
        elif normalized_load >= 1.2:
            penalty += 20
        elif normalized_load >= 0.8:
            penalty += 7

        if snapshot.disk_free_percent < 5.0:
            penalty += 40
        elif snapshot.disk_free_percent < 10.0:
            penalty += 25
        elif snapshot.disk_free_percent < 20.0:
            penalty += 8

        return max(0, min(100, 100 - penalty))

    @staticmethod
    def health_level(score: int) -> str:
        value = max(0, min(100, int(score)))
        if value >= 80:
            return "healthy"
        if value >= 60:
            return "degraded"
        if value >= 35:
            return "high"
        return "critical"

    @staticmethod
    def shadow_worker_limit(configured_workers: int, health_level: str) -> int:
        configured = max(1, min(int(configured_workers), 8))
        if health_level == "critical":
            return 1
        if health_level == "high":
            return min(configured, 2)
        if health_level == "degraded":
            return min(configured, 3)
        return configured

    @classmethod
    def stabilize_shadow_health_level(cls, score: int, previous: str | None) -> str:
        raw = cls.health_level(score)
        if previous not in _HEALTH_RANK:
            return raw
        if _HEALTH_RANK[raw] >= _HEALTH_RANK[previous]:
            return raw

        recovery_threshold = {
            "critical": 40,
            "high": 65,
            "degraded": 85,
            "healthy": 101,
        }[previous]
        if score < recovery_threshold:
            return previous

        return {
            "critical": "high",
            "high": "degraded",
            "degraded": "healthy",
            "healthy": "healthy",
        }[previous]

    def _record_shadow_comparison(self, production_workers: int, shadow_workers: int) -> int:
        """Accumulate process-local evidence without influencing admission."""
        delta = shadow_workers - production_workers
        abs_delta = abs(delta)
        self._comparison_samples += 1
        if delta == 0:
            self._comparison_agreements += 1
        else:
            self._comparison_disagreements += 1
        self._comparison_cumulative_abs_delta += abs_delta
        self._comparison_max_abs_delta = max(self._comparison_max_abs_delta, abs_delta)
        return delta

    def decide(self, configured_workers: int) -> WorkerDecision:
        configured = max(1, min(int(configured_workers), 8))
        snap = self.snapshot()
        allowed = configured
        pressure = "normal"
        reasons: list[str] = []

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

        if snap.total_memory_mib and snap.total_memory_mib < 1536:
            allowed = min(allowed, 1)
            reasons.append("small-memory host")
        elif snap.total_memory_mib and snap.total_memory_mib < 3072:
            allowed = min(allowed, 2)
            reasons.append("memory-sized concurrency cap")

        production_workers = max(1, allowed)
        reason = ", ".join(dict.fromkeys(reasons)) if reasons else "resources healthy"
        score = self.health_score(snap)
        raw_health_level = self.health_level(score)
        shadow_health_level = self.stabilize_shadow_health_level(score, self._shadow_health_level)
        self._shadow_health_level = shadow_health_level
        shadow_workers = self.shadow_worker_limit(configured, shadow_health_level)
        shadow_delta = self._record_shadow_comparison(production_workers, shadow_workers)
        return WorkerDecision(
            allowed_workers=production_workers,
            configured_workers=configured,
            pressure=pressure,
            reason=reason,
            snapshot=snap,
            health_score=score,
            health_level=raw_health_level,
            shadow_health_level=shadow_health_level,
            shadow_allowed_workers=shadow_workers,
            shadow_worker_delta=shadow_delta,
            comparison_samples=self._comparison_samples,
            comparison_agreements=self._comparison_agreements,
            comparison_disagreements=self._comparison_disagreements,
            comparison_cumulative_abs_delta=self._comparison_cumulative_abs_delta,
            comparison_max_abs_delta=self._comparison_max_abs_delta,
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
    shadow_workers = decision.shadow_allowed_workers
    if shadow_workers is None:
        shadow_workers = decision.allowed_workers
    agreement_percent = (
        decision.comparison_agreements * 100.0 / decision.comparison_samples
        if decision.comparison_samples
        else 100.0
    )
    return (
        f"Pressure: {decision.pressure}\n"
        f"Health score: {decision.health_score}/100 ({decision.health_level})\n"
        f"Workers: {decision.allowed_workers}/{decision.configured_workers}\n"
        f"Shadow health: {decision.shadow_health_level}; workers {shadow_workers}/{decision.configured_workers} "
        f"(delta {decision.shadow_worker_delta:+d})\n"
        f"Shadow comparison: {decision.comparison_agreements}/{decision.comparison_samples} agree "
        f"({agreement_percent:.1f}%); disagreements {decision.comparison_disagreements}; "
        f"cumulative |delta| {decision.comparison_cumulative_abs_delta}; max |delta| {decision.comparison_max_abs_delta}\n"
        f"Memory: {snap.available_memory_mib} MiB available / {snap.total_memory_mib} MiB total "
        f"({snap.memory_available_percent:.1f}% available)\n"
        f"Swap: {snap.swap_used_mib} MiB used / {snap.swap_total_mib} MiB total "
        f"({snap.swap_used_percent:.1f}% used)\n"
        f"CPU: load1 {snap.load1:.2f} across {snap.cpu_count} CPU(s)\n"
        f"Disk free: {snap.disk_free_percent:.1f}%\n"
        f"Memory PSI avg10: {psi}\n"
        f"Policy: {decision.reason}"
    )
