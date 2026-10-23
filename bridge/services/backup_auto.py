"""Automatic backup management: create zip, send to session, purge after 3 days."""
from __future__ import annotations

import logging
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("opencode_bridge.backup_auto")


class BackupAutoManager:
    """Creates time-stamped zip backups, delivers to session, and removes old ones."""

    def __init__(
        self,
        source_paths: list[Path],
        backup_dir: Path,
        retention_days: int = 3,
        send_callback: Callable[[Path], Awaitable[Any]] | None = None,
    ) -> None:
        self.source_paths = [Path(p) for p in source_paths]
        self.backup_dir = Path(backup_dir)
        self.retention_days = max(1, int(retention_days))
        self.send_callback = send_callback
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_zip(self, suffix: str = "") -> Path:
        now = datetime.now(UTC)
        name = f"backup_{now:%Y%m%d_%H%M%S}{('_' + suffix) if suffix else ''}.zip"
        target = self.backup_dir / name
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for src in self.source_paths:
                if not src.exists():
                    continue
                arcname = src.name if src.is_file() else src.name + "/"
                if src.is_dir():
                    for child in src.rglob("*"):
                        if child.is_file():
                            zf.write(child, arcname + str(child.relative_to(src)))
                else:
                    zf.write(src, arcname)
        log.info("backup created: %s", target)
        return target

    async def send_latest(self) -> Path | None:
        backups = sorted(self.backup_dir.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not backups:
            return None
        latest = backups[0]
        if self.send_callback is not None:
            try:
                await self.send_callback(latest)
            except Exception:
                log.warning("failed to send backup %s", latest, exc_info=True)
        return latest

    def purge_older_than_days(self, days: int | None = None) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=days or self.retention_days)
        removed = 0
        for p in self.backup_dir.glob("backup_*.zip"):
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, UTC)
                if mtime < cutoff:
                    p.unlink()
                    removed += 1
                    log.info("purged old backup: %s", p)
            except Exception:
                pass
        return removed

    async def run_cycle(self, suffix: str = "") -> Path:
        zip_path = self.create_zip(suffix)
        await self.send_latest()
        self.purge_older_than_days()
        return zip_path
