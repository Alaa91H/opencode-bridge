"""Owner-facing operational metrics derived from durable state on demand.

Design rule: **no background sampling.** A bridge that nobody is watching must
not spend CPU or I/O on metrics, so nothing here runs on a timer. The owner
asks (``/metrics``) and the snapshot is computed at that instant from two
sources that already exist:

* SQLite — per-status task counts (``GROUP BY status``, index-covered), and
* the audit log — completion durations and deferral categories, which are
  written for every task outcome anyway.

Counters therefore survive restarts and cannot drift from the database, unlike
in-memory gauges that reset on every deploy.

Known bound, stated rather than hidden: outcome metrics read only the tail of
the audit log (last 256 KiB), so a window wider than what that tail still holds
reports fewer events than actually happened. Queue gauges come from SQLite and
are always exact.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from audit_log import read_recent
from bridge.infrastructure.metrics.observability import MetricsRegistry
from bridge.infrastructure.metrics.resources import sample_resources

_DEFAULT_WINDOW_HOURS = 24


@dataclass(frozen=True)
class QueueMetrics:
    """Live queue shape, straight from the durable task table."""

    queued: int = 0
    retrying: int = 0
    scheduled: int = 0
    active: int = 0
    failed: int = 0
    completed: int = 0
    cancelled: int = 0
    wait_p50_seconds: float | None = None
    wait_p95_seconds: float | None = None
    wait_samples: int = 0

    @property
    def depth(self) -> int:
        """Work waiting for a worker: claimable now plus deferred retries."""
        return self.queued + self.retrying


def _percentile(values: tuple[float, ...], fraction: float) -> float | None:
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(max(0.0, min(fraction, 1.0)) * len(ordered)))
    return round(ordered[rank - 1], 2)


@dataclass(frozen=True)
class OutcomeMetrics:
    """Task outcomes observed inside the reporting window."""

    finished: int = 0
    succeeded: int = 0
    failed: int = 0
    deferred: int = 0
    avg_duration_seconds: float | None = None
    max_duration_seconds: float | None = None
    deferral_categories: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class MetricsSnapshot:
    queue: QueueMetrics
    outcomes: OutcomeMetrics
    resources: dict[str, float]
    worker_limit: int | None
    window_hours: int
    sampled_at: datetime


def _parse_event_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def summarize_outcomes(
    events: list[dict[str, Any]],
    *,
    since: datetime,
) -> OutcomeMetrics:
    """Fold audit events into counters; pure so the math is directly testable."""
    finished = succeeded = failed = deferred = 0
    durations: list[float] = []
    categories: dict[str, int] = {}
    for event in events:
        kind = event.get("event")
        outcome = str(event.get("outcome") or "")
        if kind not in {"task_finished", "task_deferred"}:
            continue
        stamp = _parse_event_time(event.get("timestamp"))
        # An unreadable timestamp proves nothing about the window, and these
        # events come from the bounded audit tail, so they are counted rather
        # than silently dropped from an operations report.
        if stamp is not None and stamp < since:
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if kind == "task_finished":
            finished += 1
            if outcome == "success":
                succeeded += 1
                raw = details.get("duration_seconds")
                if isinstance(raw, (int, float)) and raw >= 0:
                    durations.append(float(raw))
            else:
                failed += 1
        else:
            deferred += 1
            label = outcome or "unknown"
            categories[label] = categories.get(label, 0) + 1
    return OutcomeMetrics(
        finished=finished,
        succeeded=succeeded,
        failed=failed,
        deferred=deferred,
        avg_duration_seconds=round(sum(durations) / len(durations), 2) if durations else None,
        max_duration_seconds=round(max(durations), 2) if durations else None,
        deferral_categories=tuple(sorted(categories.items(), key=lambda item: (-item[1], item[0]))),
    )


class BridgeMetricsService:
    """Compute a metrics snapshot from the queue store and the audit log."""

    def __init__(
        self,
        store: Any,
        *,
        audit_path: Path,
        worker_limit: Callable[[], int] | None = None,
        disk_path: Path | None = None,
    ) -> None:
        self.store = store
        self.audit_path = Path(audit_path)
        self.worker_limit = worker_limit
        self.disk_path = disk_path

    def queue_metrics(
        self,
        counts: dict[str, int],
        wait_seconds: tuple[float, ...] = (),
    ) -> QueueMetrics:
        """Map the task status vocabulary (see claim_next / finish) to gauges."""
        return QueueMetrics(
            queued=int(counts.get("queued", 0)),
            retrying=int(counts.get("retrying", 0)),
            scheduled=int(counts.get("scheduled", 0)),
            active=int(counts.get("running", 0)) + int(counts.get("leased", 0)),
            failed=int(counts.get("failed", 0)) + int(counts.get("dead_letter", 0)),
            completed=int(counts.get("completed", 0)),
            cancelled=int(counts.get("cancelled", 0)),
            wait_p50_seconds=_percentile(wait_seconds, 0.50),
            wait_p95_seconds=_percentile(wait_seconds, 0.95),
            wait_samples=len(wait_seconds),
        )

    def resource_metrics(self) -> dict[str, float]:
        """Sample host resources once; never let a sampling error break /metrics."""
        registry = MetricsRegistry()
        try:
            sample_resources(registry, disk_path=self.disk_path or Path())
        except (OSError, ValueError):
            return {}
        snapshot = registry.snapshot()
        return {
            name: snapshot[name]
            for name in ("memory_bytes", "cpu_percent", "disk_free_bytes")
            if name in snapshot
        }

    def worker_limit_value(self) -> int | None:
        if self.worker_limit is None:
            return None
        try:
            return int(self.worker_limit())
        except Exception:
            return None

    async def snapshot(
        self,
        *,
        now: datetime | None = None,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
    ) -> MetricsSnapshot:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        hours = max(1, min(int(window_hours), 24 * 30))
        try:
            counts = await self.store.queue_counts()
        except Exception:
            counts = {}
        wait_seconds: tuple[float, ...] = ()
        waiter = getattr(self.store, "queue_wait_seconds", None)
        if callable(waiter):
            try:
                wait_seconds = tuple(await waiter(
                    limit=200, since=current - timedelta(hours=hours)
                ))
            except Exception:
                wait_seconds = ()
        events = read_recent(self.audit_path)
        return MetricsSnapshot(
            queue=self.queue_metrics(counts or {}, wait_seconds),
            outcomes=summarize_outcomes(events, since=current - timedelta(hours=hours)),
            resources=self.resource_metrics(),
            worker_limit=self.worker_limit_value(),
            window_hours=hours,
            sampled_at=current,
        )
