"""Secret sources with minimal in-memory exposure and deployment compatibility."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol


class SecretSource(Protocol):
    def read(self, name: str) -> str | None: ...


class SystemdCredentialSource:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(os.environ.get("CREDENTIALS_DIRECTORY", "/run/credentials"))

    def read(self, name: str) -> str | None:
        path = self.directory / name
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").rstrip("\n")


class EnvironmentSecretSource:
    """Compatibility source for gradual migration from existing .env deployment."""

    def read(self, name: str) -> str | None:
        return os.environ.get(name)


class ChainedSecretSource:
    def __init__(self, *sources: SecretSource) -> None:
        self.sources = sources

    def read(self, name: str) -> str | None:
        for source in self.sources:
            value = source.read(name)
            if value is not None:
                return value
        return None


@contextmanager
def exposed_secret(source: SecretSource, name: str) -> Iterator[str]:
    value = source.read(name)
    if value is None:
        raise KeyError(name)
    try:
        yield value
    finally:
        value = ""
