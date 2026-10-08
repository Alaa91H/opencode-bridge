"""Explicit, fail-closed authorization roles for Telegram control-plane actions.

This policy is deliberately separate from handler wiring: callers must check the
required permission before performing any administrative operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class Role(str, Enum):
    READER = "reader"
    OPERATOR = "operator"
    ADMIN = "admin"
    OWNER = "owner"


class Permission(str, Enum):
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
