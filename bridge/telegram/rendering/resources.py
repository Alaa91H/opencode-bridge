"""Telegram rendering for host-resource diagnostics."""

from __future__ import annotations

from adaptive_workers import WorkerLimitStatus
from resource_monitor import format_decision
from shadow_policy_audit import format_readiness


def format_controller(status: WorkerLimitStatus) -> str:
    recovery = (
        "ready"
        if status.recovery_remaining_seconds <= 0
        else f"{status.recovery_remaining_seconds:.1f}s remaining"
    )
    return (
        "\n\nAdaptive controller\n"
        f"Stable workers: {status.stable_workers}/{status.configured_workers}\n"
        f"Raw target: {status.raw_target_workers}\n"
        f"Controller pressure: {status.pressure}\n"
        f"Controller health: {status.health_score}/100\n"
        f"Recovery: {recovery}\n"
        f"Last policy reason: {status.reason}"
    )


def render_resource_snapshot(snapshot) -> str:
    text = "Host resources\n\n" + format_decision(snapshot.decision)
    if snapshot.controller is not None:
        text += format_controller(snapshot.controller)
    if snapshot.shadow_readiness is not None:
        text += "\n\n" + format_readiness(snapshot.shadow_readiness)
    return text
