"""Low-noise audit and rolling evaluation for production-vs-shadow policy drift."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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
    observed_for_seconds: float
    required_observation_seconds: float
    promotion_ready: bool
    reason: str


class AuditedShadowPolicy:
    """Observe a shadow resource policy without changing production admission.

    Besides low-noise divergence audit events, the wrapper keeps a bounded
    rolling window that answers whether the shadow policy has accumulated enough
    stable evidence to be considered for a future promotion. Promotion remains
    advisory only: ``allowed_workers`` is always the production decision.
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
        promotion_min_observation_seconds: float = 6 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
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
        self._promotion_min_observation_seconds = max(0.0, float(promotion_min_observation_seconds))
        self._clock = clock
        self._observation_started_at: float | None = None
        self._last_readiness: bool | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._policy, name)

    def decide(self, configured_workers: int):
        decision = self._policy.decide(configured_workers)
        self._observe(decision)
        return decision

    def readiness(self) -> ShadowReadiness:
        deltas = list(self._deltas)
        samples = len(deltas)
        agreements = sum(1 for value in deltas if value == 0)
        aggressive = sum(1 for value in deltas if value > 0)
        conservative = sum(1 for value in deltas if value < 0)
        agreement_percent = agreements * 100.0 / samples if samples else 0.0
        aggressive_percent = aggressive * 100.0 / samples if samples else 0.0
        mean_abs_delta = sum(abs(value) for value in deltas) / samples if samples else 0.0
        max_abs_delta = max((abs(value) for value in deltas), default=0)
        observed_for_seconds = (
            0.0
            if self._observation_started_at is None
            else max(0.0, self._clock() - self._observation_started_at)
        )

        checks = [
            (samples >= self._promotion_min_samples, f"need {self._promotion_min_samples} samples"),
            (
                observed_for_seconds >= self._promotion_min_observation_seconds,
                f"observation time below {self._promotion_min_observation_seconds:.0f}s",
            ),
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
        promotion_ready = all(ok for ok, _ in checks)
        reason = "shadow policy meets advisory promotion gates" if promotion_ready else next(
            message for ok, message in checks if not ok
        )
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
            observed_for_seconds=observed_for_seconds,
            required_observation_seconds=self._promotion_min_observation_seconds,
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
        if self._observation_started_at is None:
            self._observation_started_at = self._clock()
        self._deltas.append(delta)
        self._audit_readiness_transition()

        if abs(delta) < self._min_abs_delta:
            if self._last_signature is not None:
                previous_delta, previous_shadow_level = self._last_signature
                self._write_divergence(
                    "resolved",
                    decision,
                    production_workers,
                    shadow_workers,
                    delta,
                    previous_delta=previous_delta,
                    previous_shadow_level=previous_shadow_level,
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
            outcome,
            decision,
            production_workers,
            shadow_workers,
            delta,
            previous_delta=previous_delta,
            previous_shadow_level=previous_shadow_level,
        )
        self._last_signature = signature

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
                    "observed_for_seconds": round(status.observed_for_seconds, 1),
                    "required_observation_seconds": status.required_observation_seconds,
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
            self._audit_logger.write(
                "adaptive_worker_shadow_divergence",
                outcome,
                details=details,
            )
        except Exception:
            # Audit/telemetry must never affect resource-policy admission.
            pass
