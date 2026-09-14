"""Low-noise persistent audit for production-vs-shadow resource policy drift."""

from __future__ import annotations

from typing import Any


class AuditedShadowPolicy:
    """Delegate to a resource policy and audit only meaningful shadow drift changes.

    The wrapper is deliberately observational. It never changes the production
    ``allowed_workers`` decision and swallows audit failures so observability
    cannot interfere with admission control.
    """

    def __init__(self, policy: Any, audit_logger: Any, min_abs_delta: int = 1) -> None:
        self._policy = policy
        self._audit_logger = audit_logger
        self._min_abs_delta = max(1, int(min_abs_delta))
        self._last_signature: tuple[int, str] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._policy, name)

    def decide(self, configured_workers: int):
        decision = self._policy.decide(configured_workers)
        self._observe(decision)
        return decision

    def _observe(self, decision: Any) -> None:
        shadow_workers = getattr(decision, "shadow_allowed_workers", None)
        if shadow_workers is None:
            return

        production_workers = int(getattr(decision, "allowed_workers", 1))
        shadow_workers = int(shadow_workers)
        delta = int(getattr(decision, "shadow_worker_delta", shadow_workers - production_workers))
        shadow_level = str(getattr(decision, "shadow_health_level", "unknown"))

        if abs(delta) < self._min_abs_delta:
            if self._last_signature is not None:
                previous_delta, previous_shadow_level = self._last_signature
                self._write(
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
        self._write(
            outcome,
            decision,
            production_workers,
            shadow_workers,
            delta,
            previous_delta=previous_delta,
            previous_shadow_level=previous_shadow_level,
        )
        self._last_signature = signature

    def _write(
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
