"""Explicit, fail-closed authorization roles for Telegram control-plane actions.

This policy is deliberately separate from handler wiring: callers must check the
required permission before performing any administrative operation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    READER = "reader"
    OPERATOR = "operator"
    ADMIN = "admin"
    OWNER = "owner"


class Permission(StrEnum):
    READ = "read"
    OPERATE = "operate"
    ADMINISTER = "administer"
    OWNER_ONLY = "owner_only"


_ALLOWED: dict[Role, frozenset[Permission]] = {
    Role.READER: frozenset({Permission.READ}),
    Role.OPERATOR: frozenset({Permission.READ, Permission.OPERATE}),
    Role.ADMIN: frozenset({Permission.READ, Permission.OPERATE, Permission.ADMINISTER}),
    Role.OWNER: frozenset(Permission),
}


@dataclass(frozen=True)
class RolePolicy:
    """Explicit actor-to-role assignments; missing and malformed entries deny."""

    assignments: Mapping[int, Role]

    def role_for(self, actor_id: int) -> Role | None:
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            return None
        value = self.assignments.get(actor_id)
        return value if isinstance(value, Role) else None

    def allows(self, actor_id: int, permission: Permission) -> bool:
        if not isinstance(permission, Permission):
            return False
        role = self.role_for(actor_id)
        return role is not None and permission in _ALLOWED[role]


def resolve_owner_id(allowed_users: Iterable[int], explicit_owner_id: int | None) -> int | None:
    """Never infer ownership from the numeric ordering of Telegram IDs.

    An explicitly configured owner must be a positive, allowlisted user ID.
    Backward compatibility is limited to the unambiguous one-user installation.
    Multi-user installations without an explicit owner cannot administer host
    credentials, backups, software updates or reboot controls.
    """
    users = {item for item in allowed_users if type(item) is int and item > 0}
    if explicit_owner_id is not None:
        return (explicit_owner_id if type(explicit_owner_id) is int
                and explicit_owner_id in users else None)
    if len(users) != 1:
        return None
    return next(iter(users))
