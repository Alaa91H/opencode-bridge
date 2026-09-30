"""Safe ZIP inspection/extraction with traversal and expansion limits."""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafeArchive(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = 10_000
    max_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_ratio: float = 200.0


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()  # frozen config: safe to share as a default


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    compressed_size: int
    uncompressed_size: int


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeArchive(f"unsafe archive path: {name}")
    if path.parts and ":" in path.parts[0]:
        raise UnsafeArchive(f"drive-like archive path: {name}")
    return path


def zip_manifest(path: Path, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS) -> list[ArchiveEntry]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > limits.max_files:
            raise UnsafeArchive("archive file-count limit exceeded")
        total = 0
        entries = []
        for info in infos:
            _safe_name(info.filename)
            total += info.file_size
            if total > limits.max_uncompressed_bytes:
                raise UnsafeArchive("archive expansion limit exceeded")
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > limits.max_ratio:
                raise UnsafeArchive("archive compression-ratio limit exceeded")
            entries.append(ArchiveEntry(info.filename, info.compress_size, info.file_size))
        return entries


def extract_zip(path: Path, destination: Path, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS) -> list[Path]:
    manifest = zip_manifest(path, limits=limits)
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(path) as archive:
        for entry in manifest:
            relative = _safe_name(entry.name)
            target = (destination / Path(*relative.parts)).resolve()
            if target != destination and destination not in target.parents:
                raise UnsafeArchive("archive path escaped destination")
            info = archive.getinfo(entry.name)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted.append(target)
    return extracted
