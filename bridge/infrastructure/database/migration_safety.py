"""Migration metadata, compatibility windows and snapshot safety gates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationPlan:
    version: int
    sql: str
    min_compatible_version: int
    reversible: bool = False

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()

    def compatible_with(self, current_version: int) -> bool:
        return self.min_compatible_version <= current_version <= self.version


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str

    def verify(self) -> bool:
        return self.path.is_file() and hashlib.sha256(self.path.read_bytes()).hexdigest() == self.sha256


class MigrationSafety:
    def require_snapshot(self, plan: MigrationPlan, snapshot: Snapshot | None) -> None:
        if plan.reversible:
            return
        if snapshot is None or not snapshot.verify():
            raise RuntimeError("dangerous migration requires a verified database snapshot")

    def validate_checksum(self, plan: MigrationPlan, expected: str) -> None:
        if plan.checksum != expected:
            raise RuntimeError("migration checksum mismatch")

    def rollback_strategy(self, plan: MigrationPlan) -> str:
        return "reverse_migration" if plan.reversible else "restore_snapshot"
