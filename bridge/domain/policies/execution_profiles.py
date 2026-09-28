"""Execution capability profiles. SAFE is always the default."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ExecutionProfile(str, Enum):
    SAFE = "safe"
    DEVELOPMENT = "development"
    POWER = "power"
    HOST_ADMIN = "host_admin"


CAPABILITIES: dict[ExecutionProfile, frozenset[str]] = {
    ExecutionProfile.SAFE: frozenset({"read_workspace", "write_workspace", "run_safe_tools"}),
    ExecutionProfile.DEVELOPMENT: frozenset({"read_workspace", "write_workspace", "run_safe_tools", "build", "install_sandbox_dependencies", "git_commit"}),
    ExecutionProfile.POWER: frozenset({"read_workspace", "write_workspace", "run_safe_tools", "build", "install_sandbox_dependencies", "git_commit", "network_sandbox", "long_running"}),
    ExecutionProfile.HOST_ADMIN: frozenset({"read_workspace", "write_workspace", "run_safe_tools", "build", "install_sandbox_dependencies", "git_commit", "network_sandbox", "long_running", "host_admin"}),
}


@dataclass(frozen=True)
class ProfileSelection:
    profile: ExecutionProfile
    source: str


@dataclass(frozen=True)
class EscalationAudit:
    owner: str
    task_id: str | None
    previous: ExecutionProfile
    requested: ExecutionProfile
    allowed: bool
    reason: str


class ExecutionPolicy:
    def __init__(self, *, host_admin_allowlist: frozenset[str] = frozenset(),
                 audit: Callable[[EscalationAudit], None] | None = None) -> None:
        self.host_admin_allowlist = host_admin_allowlist
        self.audit = audit

    def select(self, *, owner: str, task_profile: ExecutionProfile | None = None,
               user_profile: ExecutionProfile | None = None, task_id: str | None = None) -> ProfileSelection:
        requested = task_profile or user_profile or ExecutionProfile.SAFE
        source = "task" if task_profile else "user" if user_profile else "default"
        if requested is ExecutionProfile.HOST_ADMIN and owner not in self.host_admin_allowlist:
            self._audit(owner, task_id, ExecutionProfile.SAFE, requested, False, "owner not in HOST_ADMIN allowlist")
            return ProfileSelection(ExecutionProfile.SAFE, "host_admin_denied")
        if requested is not ExecutionProfile.SAFE:
            self._audit(owner, task_id, ExecutionProfile.SAFE, requested, True, source)
        return ProfileSelection(requested, source)

    def _audit(self, owner: str, task_id: str | None, previous: ExecutionProfile,
               requested: ExecutionProfile, allowed: bool, reason: str) -> None:
        if self.audit:
            self.audit(EscalationAudit(owner, task_id, previous, requested, allowed, reason))

    @staticmethod
    def allows(profile: ExecutionProfile, capability: str) -> bool:
        return capability in CAPABILITIES[profile]

    @staticmethod
    def sanitize_environment(environment: dict[str, str]) -> dict[str, str]:
        secret_markers = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY", "CREDENTIAL")
        return {k: v for k, v in environment.items() if not any(marker in k.upper() for marker in secret_markers)}
