"""Atomic releases/version + current symlink deployment pipeline."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReleaseArtifact:
    version: str
    source: Path
    sha256: str


class AtomicDeployment:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.releases = root / "releases"
        self.current = root / "current"

    async def deploy(
        self, artifact: ReleaseArtifact, *,
        test: Callable[[Path], Awaitable[None]],
        migrate: Callable[[Path], Awaitable[None]],
        health: Callable[[Path], Awaitable[None]],
        restart: Callable[[], Awaitable[None]],
        smoke: Callable[[Path], Awaitable[None]],
    ) -> Path:
        payload = artifact.source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise ValueError("release checksum mismatch")
        self.releases.mkdir(parents=True, exist_ok=True)
        target = self.releases / artifact.version
        previous = self._current_target()
        if target.exists():
            raise FileExistsError(target)
        target.mkdir()
        shutil.copy2(artifact.source, target / artifact.source.name)
        await test(target)
        await migrate(target)
        await health(target)
        self._switch(target)
        try:
            await restart()
            await smoke(target)
        except BaseException:
            if previous is not None:
                self._switch(previous)
                await restart()
            raise
        return target

    def _current_target(self) -> Path | None:
        if not self.current.is_symlink():
            return None
        return self.current.resolve()

    def _switch(self, target: Path) -> None:
        temp = self.root / ".current-next"
        temp.unlink(missing_ok=True)
        temp.symlink_to(target)
        os.replace(temp, self.current)
