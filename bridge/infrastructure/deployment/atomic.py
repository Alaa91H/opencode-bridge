"""Atomic releases/version + current symlink deployment pipeline."""

from __future__ import annotations

import hashlib
import os
import re
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
        self.root = root.resolve()
        self.releases = self.root / "releases"
        self.current = self.root / "current"

    async def deploy(
        self, artifact: ReleaseArtifact, *,
        test: Callable[[Path], Awaitable[None]],
        migrate: Callable[[Path], Awaitable[None]],
        health: Callable[[Path], Awaitable[None]],
        restart: Callable[[], Awaitable[None]],
        smoke: Callable[[Path], Awaitable[None]],
    ) -> Path:
        # Version is a directory component, never an arbitrary path. Reject
        # traversal and special dot names before touching the release tree.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", artifact.version):
            raise ValueError("invalid release version")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", artifact.sha256):
            raise ValueError("invalid release checksum")
        digest = hashlib.sha256()
        with artifact.source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest().lower() != artifact.sha256.lower():
            raise ValueError("release checksum mismatch")
        if artifact.source.is_symlink() or not artifact.source.is_file():
            raise ValueError("release artifact must be an ordinary file")
        # The active tree and release store must never be redirected through
        # a symlink to an arbitrary directory outside this deployment root.
        if self.releases.is_symlink():
            raise ValueError("release directory cannot be a symlink")
        self.releases.mkdir(parents=True, exist_ok=True)
        target = self.releases / artifact.version
        previous = self._current_target()
        if target.exists():
            raise FileExistsError(target)
        target.mkdir()
        try:
            copy = target / artifact.source.name
            shutil.copy2(artifact.source, copy)
            # Catch mutations to the source between the initial checksum and
            # the copy; only an independently verified copy may be activated.
            copied_digest = hashlib.sha256()
            with copy.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    copied_digest.update(block)
            if copied_digest.hexdigest().lower() != artifact.sha256.lower():
                raise ValueError("copied release checksum mismatch")
            await test(target)
            await migrate(target)
            await health(target)
        except BaseException:
            shutil.rmtree(target)
            raise
        self._switch(target)
        try:
            await restart()
            await smoke(target)
        except BaseException:
            if previous is not None:
                self._switch(previous)
                await restart()
            else:
                # Never leave a failing first deployment marked as current.
                self.current.unlink(missing_ok=True)
            raise
        return target

    def _current_target(self) -> Path | None:
        if not self.current.is_symlink():
            if self.current.exists():
                raise ValueError("current release must be a symlink")
            return None
        try:
            target = self.current.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("current release symlink cannot be resolved") from exc
        self._verify_release_target(target)
        return target

    def _verify_release_target(self, target: Path) -> None:
        if self.releases.is_symlink() or target.is_symlink():
            raise ValueError("release target cannot be a symlink")
        try:
            resolved = target.resolve(strict=True)
            base = self.releases.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("release target cannot be resolved") from exc
        if not resolved.is_dir() or resolved.parent != base:
            raise ValueError("release target escapes the release directory")

    def _switch(self, target: Path) -> None:
        self._verify_release_target(target)
        temp = self.root / ".current-next"
        temp.unlink(missing_ok=True)
        temp.symlink_to(target)
        os.replace(temp, self.current)
