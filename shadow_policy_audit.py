"""Low-noise audit and rolling evaluation for production-vs-shadow policy drift."""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_STATE_VERSION = 1


@dataclass(frozen=True)
class ShadowReadiness:
    samples: int
    window_size: int
    agreements: int
    agreement_percent: float
    mean_abs_delta: float
    max_abs_delta: int
    aggressive_samples: int
    conservative_samples: int
    aggressive_percent: float
    statistical_ready: bool
    stable_for_seconds: float
    required_stable_seconds: float
    promotion_ready: bool
    reason: str


def format_readiness(status: ShadowReadiness) -> str:
    """Render compact advisory diagnostics without implying automatic promotion."""
    state = "ready" if status.promotion_ready else "not ready"
    return (
        "Shadow readiness (advisory only)\n"
        f"State: {state}; {status.reason}\n"
        f"Window: {status.samples}/{status.window_size} samples; agreement {status.agreement_percent:.1f}%\n"
        f"Delta: mean |delta| {status.mean_abs_delta:.3f}; max |delta| {status.max_abs_delta}\n"
        f"Bias: aggressive {status.aggressive_samples} ({status.aggressive_percent:.1f}%); "
        f"conservative {status.conservative_samples}\n"
        f"Stable evidence: {status.stable_for_seconds:.0f}/{status.required_stable_seconds:.0f}s"
    )


class AuditedShadowPolicy:
    """Observe a shadow resource policy without changing production admission.

    Besides low-noise divergence audit events, the wrapper keeps a bounded
    rolling window that answers whether the shadow policy has accumulated enough
    continuously stable evidence to be considered for a future promotion.
    Promotion remains advisory only: ``allowed_workers`` is always production.

    Optional runtime persistence retains recent evidence across short service
    restarts. Persisted state is bounded, atomically replaced, age-validated,
    and never counts service downtime as additional stable evidence.
    """

    def __init__(
        self,
        policy: Any,
        audit_logger: Any,
        min_abs_delta: int = 1,
        *,
        evaluation_window: int = 60,
        promotion_min_samples: int = 30,
        promotion_min_agreement_percent: float = 95.0,
        promotion_max_mean_abs_delta: float = 0.10,
        promotion_max_abs_delta: int = 1,
        promotion_max_aggressive_percent: float = 2.0,
        promotion_min_stable_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        state_path: str | Path | None = None,
        persist_interval_seconds: float = 60.0,
        max_state_age_seconds: float = 15 * 60,
    ) -> None:
        self._policy = policy
        self._audit_logger = audit_logger
        self._min_abs_delta = max(1, int(min_abs_delta))
        self._last_signature: tuple[int, str] | None = None
        self._deltas: deque[int] = deque(maxlen=max(5, int(evaluation_window)))
        self._promotion_min_samples = max(5, min(int(promotion_min_samples), self._deltas.maxlen or 60))
        self._promotion_min_agreement_percent = max(0.0, min(100.0, float(promotion_min_agreement_percent)))
        self._promotion_max_mean_abs_delta = max(0.0, float(promotion_max_mean_abs_delta))
        self._promotion_max_abs_delta = max(0, int(promotion_max_abs_delta))
        self._promotion_max_aggressive_percent = max(0.0, min(100.0, float(promotion_max_aggressive_percent)))
        self._promotion_min_stable_seconds = max(0.0, float(promotion_min_stable_seconds))
        self._clock = clock
        self._wall_clock = wall_clock
        self._state_path = Path(state_path) if state_path is not None else None
        self._persist_interval_seconds = max(0.0, float(persist_interval_seconds))
        self._max_state_age_seconds = max(0.0, float(max_state_age_seconds))
        self._last_persist_at: float | None = None
        self._statistically_ready_since: float | None = None
        self._last_readiness: bool | None = None
        self._restore_state()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._policy, name)

    def decide(self, configured_workers: int):
        decision = self._policy.decide(configured_workers)
        self._observe(decision)
        return decision

    def _statistics(self) -> tuple[int, int, int, int, float, float, int, list[tuple[bool, str]]]:
        deltas = list(self._deltas)
        samples = len(deltas)
        agreements = sum(1 for value in deltas if value == 0)
        aggressive = sum(1 for value in deltas if value > 0)
        conservative = sum(1 for value in deltas if value < 0)
        agreement_percent = agreements * 100.0 / samples if samples else 0.0
        aggressive_percent = aggressive * 100.0 / samples if samples else 0.0
        mean_abs_delta = sum(abs(value) for value in deltas) / samples if samples else 0.0
        max_abs_delta = max((abs(value) for value in deltas), default=0)
        checks = [
            (samples >= self._promotion_min_samples, f"need {self._promotion_min_samples} samples"),
            (
                agreement_percent >= self._promotion_min_agreement_percent,
                f"agreement below {self._promotion_min_agreement_percent:.1f}%",
            ),
            (
                mean_abs_delta <= self._promotion_max_mean_abs_delta,
                f"mean |delta| above {self._promotion_max_mean_abs_delta:.2f}",
            ),
            (max_abs_delta <= self._promotion_max_abs_delta, f"max |delta| above {self._promotion_max_abs_delta}"),
            (
                aggressive_percent <= self._promotion_max_aggressive_percent,
                f"aggressive rate above {self._promotion_max_aggressive_percent:.1f}%",
            ),
        ]
        return (
            samples,
            agreements,
            aggressive,
            conservative,
            agreement_percent,
            aggressive_percent,
            max_abs_delta,
            checks,
        )

    def readiness(self) -> ShadowReadiness:
        deltas = list(self._deltas)
        (
            samples,
            agreements,
            aggressive,
            conservative,
            agreement_percent,
            aggressive_percent,
            max_abs_delta,
            checks,
        ) = self._statistics()
        mean_abs_delta = sum(abs(value) for value in deltas) / samples if samples else 0.0
        statistical_ready = all(ok for ok, _ in checks)
        now = self._clock()
        if statistical_ready:
            if self._statistically_ready_since is None:
                self._statistically_ready_since = now
            stable_for_seconds = max(0.0, now - self._statistically_ready_since)
        else:
            self._statistically_ready_since = None
            stable_for_seconds = 0.0

        duration_ready = stable_for_seconds >= self._promotion_min_stable_seconds
        promotion_ready = statistical_ready and duration_ready
        if not statistical_ready:
            reason = next(message for ok, message in checks if not ok)
        elif not duration_ready:
            remaining = max(0.0, self._promotion_min_stable_seconds - stable_for_seconds)
            reason = f"need {remaining:.0f}s more continuously stable evidence"
        else:
            reason = "shadow policy meets advisory promotion gates"

        return ShadowReadiness(
            samples=samples,
            window_size=self._deltas.maxlen or samples,
            agreements=agreements,
            agreement_percent=agreement_percent,
            mean_abs_delta=mean_abs_delta,
            max_abs_delta=max_abs_delta,
            aggressive_samples=aggressive,
            conservative_samples=conservative,
            aggressive_percent=aggressive_percent,
            statistical_ready=statistical_ready,
            stable_for_seconds=stable_for_seconds,
            required_stable_seconds=self._promotion_min_stable_seconds,
            promotion_ready=promotion_ready,
            reason=reason,
        )

    def _observe(self, decision: Any) -> None:
        shadow_workers = getattr(decision, "shadow_allowed_workers", None)
        if shadow_workers is None:
            return

        production_workers = int(getattr(decision, "allowed_workers", 1))
        shadow_workers = int(shadow_workers)
        delta = int(getattr(decision, "shadow_worker_delta", shadow_workers - production_workers))
        shadow_level = str(getattr(decision, "shadow_health_level", "unknown"))
        self._deltas.append(delta)
        self._audit_readiness_transition()
        self._persist_state_if_due()

        if abs(delta) < self._min_abs_delta:
            if self._last_signature is not None:
                previous_delta, previous_shadow_level = self._last_signature
                self._write_divergence(
                    "resolved", decision, production_workers, shadow_workers, delta,
                    previous_delta=previous_delta, previous_shadow_level=previous_shadow_level,
                )
                self._last_signature = None
            return

        signature = (delta, shadow_level)
        if signature == self._last_signature:
            return

        outcome = "started" if self._last_signature is None else "changed"
        previous_delta = None if self._last_signature is None else self._last_signature[0]
        previous_shadow_level = None if self._last_signature is None else self._last_signature[1]
        self._write_divergence(
            outcome, decision, production_workers, shadow_workers, delta,
            previous_delta=previous_delta, previous_shadow_level=previous_shadow_level,
        )
        self._last_signature = signature

    def _restore_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if payload.get("version") != _STATE_VERSION:
                raise ValueError("unsupported state version")
            saved_at = float(payload["saved_at_unix"])
            now_wall = float(self._wall_clock())
            if not math.isfinite(saved_at) or saved_at > now_wall + 5.0:
                raise ValueError("invalid state timestamp")
            age = max(0.0, now_wall - saved_at)
            if age > self._max_state_age_seconds:
                raise ValueError("state too old")
            raw_deltas = payload.get("deltas")
            if not isinstance(raw_deltas, list) or len(raw_deltas) > (self._deltas.maxlen or 60):
                raise ValueError("invalid delta window")
            deltas: list[int] = []
            for value in raw_deltas:
                if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 8:
                    raise ValueError("invalid delta value")
                deltas.append(value)
            self._deltas.extend(deltas)

            stable_elapsed = float(payload.get("stable_elapsed_seconds", 0.0))
            if not math.isfinite(stable_elapsed) or stable_elapsed < 0.0:
                raise ValueError("invalid stable duration")
            stable_elapsed = min(stable_elapsed, self._promotion_min_stable_seconds)
            checks = self._statistics()[-1]
            if deltas and all(ok for ok, _ in checks) and stable_elapsed > 0.0:
                self._statistically_ready_since = self._clock() - stable_elapsed
            self._last_persist_at = self._clock()
            self._audit_state("restored", {"samples": len(deltas), "age_seconds": round(age, 1)})
        except Exception as exc:
            self._deltas.clear()
            self._statistically_ready_since = None
            self._audit_state("ignored", {"reason": type(exc).__name__})

    def _persist_state_if_due(self) -> None:
        if self._state_path is None:
            return
        now = self._clock()
        if self._last_persist_at is not None and now - self._last_persist_at < self._persist_interval_seconds:
            return
        status = self.readiness()
        payload = {
            "version": _STATE_VERSION,
            "saved_at_unix": float(self._wall_clock()),
            "deltas": list(self._deltas),
            "stable_elapsed_seconds": status.stable_for_seconds if status.statistical_ready else 0.0,
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_path.with_name(self._state_path.name + ".tmp")
            tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, self._state_path)
            self._last_persist_at = now
        except Exception:
            # Persistence is observability-only and must never affect admission.
            pass

    def _audit_state(self, outcome: str, details: dict[str, Any]) -> None:
        try:
            self._audit_logger.write(
                "adaptive_worker_shadow_state",
                outcome,
                details={**details, "advisory_only": True},
            )
        except Exception:
            pass

    def _audit_readiness_transition(self) -> None:
        status = self.readiness()
        if status.samples < self._promotion_min_samples:
            return
        if status.promotion_ready == self._last_readiness:
            return
        self._last_readiness = status.promotion_ready
        try:
            self._audit_logger.write(
                "adaptive_worker_shadow_readiness",
                "ready" if status.promotion_ready else "not_ready",
                details={
                    "samples": status.samples,
                    "window_size": status.window_size,
                    "agreement_percent": round(status.agreement_percent, 2),
                    "mean_abs_delta": round(status.mean_abs_delta, 3),
                    "max_abs_delta": status.max_abs_delta,
                    "aggressive_samples": status.aggressive_samples,
                    "conservative_samples": status.conservative_samples,
                    "aggressive_percent": round(status.aggressive_percent, 2),
                    "statistical_ready": status.statistical_ready,
                    "stable_for_seconds": round(status.stable_for_seconds, 1),
                    "required_stable_seconds": status.required_stable_seconds,
                    "reason": status.reason,
                    "advisory_only": True,
                },
            )
        except Exception:
            pass

    def _write_divergence(
        self,
        outcome: str,
        decision: Any,
        production_workers: int,
        shadow_workers: int,
        delta: int,
        *,
        previous_delta: int | None,
        previous_shadow_level: str | None,
    ) -> None:
        details = {
            "production_workers": production_workers,
            "shadow_workers": shadow_workers,
            "shadow_worker_delta": delta,
            "abs_delta": abs(delta),
            "health_score": int(getattr(decision, "health_score", 100)),
            "health_level": str(getattr(decision, "health_level", "unknown")),
            "shadow_health_level": str(getattr(decision, "shadow_health_level", "unknown")),
            "comparison_samples": int(getattr(decision, "comparison_samples", 0)),
            "comparison_agreements": int(getattr(decision, "comparison_agreements", 0)),
            "comparison_disagreements": int(getattr(decision, "comparison_disagreements", 0)),
            "comparison_max_abs_delta": int(getattr(decision, "comparison_max_abs_delta", abs(delta))),
            "previous_delta": previous_delta,
            "previous_shadow_health_level": previous_shadow_level,
        }
        try:
            self._audit_logger.write("adaptive_worker_shadow_divergence", outcome, details=details)
        except Exception:
            # Audit/telemetry must never affect resource-policy admission.
            pass
