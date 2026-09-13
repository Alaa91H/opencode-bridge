"""Periodic host watchdog with bounded, reversible recovery actions."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_log import AuditLogger
from opencode_client import OpenCodeClient
from resource_monitor import HostResourcePolicy, ResourceSnapshot
from watchdog_policy import (
    OPENCODE_SERVICE,
    TELEGRAM_SERVICE,
    QueueHealth,
    ServiceHealth,
    UpdateHealth,
    assess_health,
    restart_allowed,
)

BRIDGE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BRIDGE_DIR / "runtime"
DB_PATH = BRIDGE_DIR / "sessions.db"
STATE_PATH = RUNTIME_DIR / "watchdog-state.json"
REPORT_PATH = RUNTIME_DIR / "watchdog-latest.json"
AUDIT_PATH = RUNTIME_DIR / "watchdog-audit.jsonl"
DEPLOYED_REF_PATH = RUNTIME_DIR / "deployed-ref"
UTC = timezone.utc


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BRIDGE_DIR / ".env")
STALE_TASK_SECONDS = max(300, int(os.environ.get("WATCHDOG_STALE_TASK_SECONDS", "7200")))
RESTART_COOLDOWN_SECONDS = max(60, int(os.environ.get("WATCHDOG_RESTART_COOLDOWN_SECONDS", "300")))
MAX_RESTARTS_PER_HOUR = max(1, min(int(os.environ.get("WATCHDOG_MAX_RESTARTS_PER_HOUR", "3")), 10))


def _run(*args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=BRIDGE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def _service_active(service: str) -> bool:
    try:
        return _run("systemctl", "--user", "is-active", "--quiet", service).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _restart_service(service: str) -> bool:
    try:
        result = _run("systemctl", "--user", "restart", service, timeout=30.0)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


async def _opencode_health_async() -> bool:
    client = OpenCodeClient(
        host=os.environ.get("OPENCODE_HOST", "127.0.0.1"),
        port=int(os.environ.get("OPENCODE_PORT", "4096")),
        password=os.environ.get("OPENCODE_PASSWORD") or os.environ.get("OPENCODE_SERVER_PASSWORD"),
        timeout=10.0,
    )
    try:
        return await client.health_check()
    finally:
        await client.close()


def _opencode_healthy() -> bool:
    try:
        return asyncio.run(_opencode_health_async())
    except Exception:
        return False


def _queue_health(reference: datetime | None = None) -> QueueHealth:
    if not DB_PATH.exists():
        return QueueHealth(readable=False)
    now = reference or datetime.now(UTC)
    try:
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT "
                "SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued, "
                "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running "
                "FROM agent_tasks"
            ).fetchone()
            queued = int((row["queued"] if row else 0) or 0)
            running = int((row["running"] if row else 0) or 0)
            stale = 0
            for item in connection.execute(
                "SELECT started_at FROM agent_tasks WHERE status='running' AND started_at IS NOT NULL"
            ):
                try:
                    started = datetime.fromisoformat(str(item["started_at"]))
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=UTC)
                    if (now - started.astimezone(UTC)).total_seconds() > STALE_TASK_SECONDS:
                        stale += 1
                except ValueError:
                    stale += 1
            return QueueHealth(queued=queued, running=running, stale_running=stale, readable=True)
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return QueueHealth(readable=False)


def _git_count(revision_range: str) -> int | None:
    try:
        result = _run("git", "rev-list", "--count", revision_range)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _update_health() -> UpdateHealth:
    try:
        head_result = _run("git", "rev-parse", "HEAD")
        if head_result.returncode != 0:
            return UpdateHealth()
        head = head_result.stdout.strip()
        deployed_match: bool | None = None
        if DEPLOYED_REF_PATH.exists():
            deployed = DEPLOYED_REF_PATH.read_text(encoding="utf-8").strip().split()[0]
            deployed_match = deployed == head
        return UpdateHealth(
            deployed_matches_head=deployed_match,
            behind_origin_main=_git_count("HEAD..refs/remotes/origin/main"),
        )
    except (OSError, IndexError):
        return UpdateHealth()


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE_PATH)


def _resource_payload(snapshot: ResourceSnapshot) -> dict[str, Any]:
    return {
        "total_memory_mib": snapshot.total_memory_mib,
        "available_memory_mib": snapshot.available_memory_mib,
        "swap_total_mib": snapshot.swap_total_mib,
        "swap_free_mib": snapshot.swap_free_mib,
        "cpu_count": snapshot.cpu_count,
        "load1": snapshot.load1,
        "disk_free_percent": round(snapshot.disk_free_percent, 2),
        "memory_psi_avg10": snapshot.memory_psi_avg10,
    }


def _collect() -> tuple[ResourceSnapshot, ServiceHealth, QueueHealth, UpdateHealth]:
    resources = HostResourcePolicy(cache_seconds=0.5).snapshot(force=True)
    opencode_active = _service_active(OPENCODE_SERVICE)
    services = ServiceHealth(
        opencode_service_active=opencode_active,
        opencode_http_healthy=_opencode_healthy() if opencode_active else False,
        telegram_service_active=_service_active(TELEGRAM_SERVICE),
    )
    return resources, services, _queue_health(), _update_health()


def _write_report(
    resources: ResourceSnapshot,
    services: ServiceHealth,
    queue: QueueHealth,
    update: UpdateHealth,
    actions: list[dict[str, str]],
) -> dict[str, Any]:
    assessment = assess_health(resources, services, queue, update)
    manual_required = assessment.manual_action_required or any(
        action["outcome"] in {"skipped", "restart_failed"} for action in actions
    )
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "status": assessment.status,
        "score": assessment.score,
        "issues": list(assessment.issues),
        "manual_action_required": manual_required,
        "resources": _resource_payload(resources),
        "services": {
            "opencode_active": services.opencode_service_active,
            "opencode_http_healthy": services.opencode_http_healthy,
            "telegram_bridge_active": services.telegram_service_active,
        },
        "queue": {
            "queued": queue.queued,
            "running": queue.running,
            "stale_running": queue.stale_running,
            "readable": queue.readable,
        },
        "update": {
            "deployed_matches_head": update.deployed_matches_head,
            "behind_origin_main": update.behind_origin_main,
        },
        "actions": actions,
    }
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o640)
    temporary.replace(REPORT_PATH)
    return payload


def main() -> int:
    audit = AuditLogger(AUDIT_PATH)
    state = _load_state()
    history = state.setdefault("restart_history", {})
    resources, services, queue, update = _collect()
    assessment = assess_health(resources, services, queue, update)
    actions: list[dict[str, str]] = []
    now = time.time()

    for service in assessment.recoverable_services:
        if service == TELEGRAM_SERVICE:
            actions.append(
                {
                    "service": service,
                    "outcome": "delegated",
                    "reason": "bridge recovery is owned by systemd Restart=on-failure",
                }
            )
            continue
        allowed, reason = restart_allowed(
            service,
            resources,
            history,
            now=now,
            cooldown_seconds=RESTART_COOLDOWN_SECONDS,
            max_restarts_per_hour=MAX_RESTARTS_PER_HOUR,
        )
        if not allowed:
            actions.append({"service": service, "outcome": "skipped", "reason": reason})
            continue
        succeeded = _restart_service(service)
        if succeeded:
            history.setdefault(service, []).append(now)
        outcome = "restarted" if succeeded else "restart_failed"
        actions.append({"service": service, "outcome": outcome, "reason": reason})
        audit.write("watchdog_recovery", outcome, details={"service": service, "reason": reason})

    if any(action["outcome"] == "restarted" for action in actions):
        time.sleep(3.0)
        resources, services, queue, update = _collect()

    report = _write_report(resources, services, queue, update, actions)
    _save_state(state)
    audit.write(
        "watchdog_cycle",
        str(report["status"]),
        details={
            "score": report["score"],
            "issues": report["issues"],
            "manual_action_required": report["manual_action_required"],
            "actions": actions,
        },
    )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
