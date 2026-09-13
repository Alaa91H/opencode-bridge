"""Pure health-scoring and restart-guard policy for the service watchdog."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from resource_monitor import ResourceSnapshot

OPENCODE_SERVICE = "opencode-serve.service"
TELEGRAM_SERVICE = "opencode-bridge-telegram.service"
ALLOWED_SERVICES = (OPENCODE_SERVICE, TELEGRAM_SERVICE)


@dataclass(frozen=True)
class QueueHealth:
    queued: int = 0
    running: int = 0
    stale_running: int = 0
    readable: bool = True


@dataclass(frozen=True)
class ServiceHealth:
    opencode_service_active: bool
    opencode_http_healthy: bool
    telegram_service_active: bool


@dataclass(frozen=True)
class UpdateHealth:
    deployed_matches_head: bool | None = None
    behind_origin_main: int | None = None


@dataclass(frozen=True)
class WatchdogAssessment:
    score: int
    status: str
    issues: tuple[str, ...]
    recoverable_services: tuple[str, ...]
    manual_action_required: bool


def assess_health(
    resources: ResourceSnapshot,
    services: ServiceHealth,
    queue: QueueHealth,
    update: UpdateHealth,
) -> WatchdogAssessment:
    score = 100
    issues: list[str] = []
    recoverable: list[str] = []
    manual = False

    if not services.opencode_service_active:
        score -= 30
        issues.append("OpenCode service is inactive")
        recoverable.append(OPENCODE_SERVICE)
    elif not services.opencode_http_healthy:
        score -= 20
        issues.append("OpenCode HTTP health check failed")
        recoverable.append(OPENCODE_SERVICE)

    if not services.telegram_service_active:
        score -= 30
        issues.append("Telegram bridge service is inactive")
        recoverable.append(TELEGRAM_SERVICE)

    memory_percent = (
        resources.available_memory_mib * 100.0 / resources.total_memory_mib
        if resources.total_memory_mib > 0
        else 0.0
    )
    normalized_load = resources.load1 / max(1, resources.cpu_count)

    if resources.available_memory_mib < 128 or memory_percent < 6:
        score -= 25
        issues.append("critically low available memory")
        manual = True
    elif resources.available_memory_mib < 384 or memory_percent < 12:
        score -= 15
        issues.append("high memory pressure")

    if resources.memory_psi_avg10 is not None and resources.memory_psi_avg10 >= 10.0:
        score -= 15
        issues.append("sustained memory stalls")
        manual = True
    elif resources.memory_psi_avg10 is not None and resources.memory_psi_avg10 >= 2.0:
        score -= 7
        issues.append("memory stalls detected")

    if normalized_load >= 2.0:
        score -= 12
        issues.append("CPU saturation")
    elif normalized_load >= 1.2:
        score -= 5
        issues.append("high CPU load")

    if resources.disk_free_percent < 2.0:
        score -= 25
        issues.append("critically low disk space")
        manual = True
    elif resources.disk_free_percent < 5.0:
        score -= 15
        issues.append("critical disk pressure")
        manual = True
    elif resources.disk_free_percent < 10.0:
        score -= 7
        issues.append("low disk space")

    if not queue.readable:
        score -= 10
        issues.append("queue database health check failed")
    else:
        if queue.stale_running:
            score -= 15
            issues.append(f"{queue.stale_running} running task(s) exceeded the stale threshold")
            manual = True
        if queue.queued >= 50:
            score -= 10
            issues.append(f"queue backlog is high ({queue.queued})")
        elif queue.queued >= 20:
            score -= 5
            issues.append(f"queue backlog is elevated ({queue.queued})")

    if update.deployed_matches_head is False:
        score -= 10
        issues.append("deployed revision does not match the working revision")
    if update.behind_origin_main and update.behind_origin_main > 0:
        score -= 5
        issues.append(f"local revision is behind origin/main by {update.behind_origin_main} commit(s)")

    score = max(0, min(score, 100))
    if score >= 90 and not issues:
        status = "healthy"
    elif score >= 60:
        status = "degraded"
    else:
        status = "critical"
        manual = True

    return WatchdogAssessment(
        score=score,
        status=status,
        issues=tuple(issues),
        recoverable_services=tuple(dict.fromkeys(recoverable)),
        manual_action_required=manual,
    )


def restart_allowed(
    service: str,
    resources: ResourceSnapshot,
    restart_history: dict[str, list[float]],
    *,
    now: float | None = None,
    cooldown_seconds: int = 300,
    max_restarts_per_hour: int = 3,
) -> tuple[bool, str]:
    """Allow only bounded, reversible restarts of known user services."""
    if service not in ALLOWED_SERVICES:
        return False, "service is not allow-listed"
    if resources.available_memory_mib < 128 or resources.disk_free_percent < 2.0:
        return False, "host resources are too constrained for automatic recovery"

    current = time.time() if now is None else float(now)
    values = restart_history.setdefault(service, [])
    recent = [float(value) for value in values if current - float(value) < 3600]
    restart_history[service] = recent
    if recent and current - recent[-1] < max(60, int(cooldown_seconds)):
        return False, "restart cooldown is active"
    if len(recent) >= max(1, int(max_restarts_per_hour)):
        return False, "restart loop guard reached its hourly limit"
    return True, "restart is allowed"
