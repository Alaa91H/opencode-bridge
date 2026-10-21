"""Bounded ZIP inspection and extraction with safe destinations."""

from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafeArchive(ValueError):
    """A ZIP member is unsafe, ambiguous or exceeds the extraction policy."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = 10_000
    max_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_ratio: float = 200.0


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    compressed_size: int
    uncompressed_size: int


def _safe_name(name: str) -> PurePosixPath:
    if not name or "\x00" in name:
        raise UnsafeArchive("empty or NUL-containing archive member")
    path = PurePosixPath(name.replace("\\", "/"))
    if path == PurePosixPath(".") or path.is_absolute() or ".." in path.parts:
        raise UnsafeArchive(f"unsafe archive path: {name!r}")
    if path.parts and ":" in path.parts[0]:
        raise UnsafeArchive(f"drive-like archive path: {name!r}")
    return path


def _checked_infos(archive: zipfile.ZipFile, limits: ArchiveLimits) -> list[zipfile.ZipInfo]:
    if limits.max_files < 1 or limits.max_uncompressed_bytes < 0 or limits.max_ratio <= 0:
        raise ValueError("invalid archive limits")
    infos = archive.infolist()
    if len(infos) > limits.max_files:
        raise UnsafeArchive("archive file-count limit exceeded")
    seen: set[PurePosixPath] = set()
    total = 0
    for info in infos:
        relative = _safe_name(info.filename)
        if relative in seen:
            raise UnsafeArchive(f"duplicate archive member: {info.filename!r}")
        seen.add(relative)
        # ZIP external attributes encode Unix file types. Never materialize
        # symlinks, devices, pipes or other special filesystem nodes.
        kind = (info.external_attr >> 16) & 0o170000
        if kind not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG):
            raise UnsafeArchive(f"unsupported archive member type: {info.filename!r}")
        total += info.file_size
        if total > limits.max_uncompressed_bytes:
            raise UnsafeArchive("archive expansion limit exceeded")
        if info.file_size / max(1, info.compress_size) > limits.max_ratio:
            raise UnsafeArchive("archive compression-ratio limit exceeded")
    return infos


def zip_manifest(path: Path, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS) -> list[ArchiveEntry]:
    with zipfile.ZipFile(path) as archive:
        return [ArchiveEntry(info.filename, info.compress_size, info.file_size)
                for info in _checked_infos(archive, limits)]


def extract_zip(path: Path, destination: Path, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS) -> list[Path]:
    """Extract validated regular files exclusively; never replace existing data.

    The opened archive is checked and consumed in one pass to avoid a mismatch
    between a separately inspected manifest and the bytes actually extracted.
    On extraction errors, only files and directories created by this operation
    are removed; pre-existing output data is never changed.
    """
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    created_dirs: list[Path] = []
    total_written = 0

    def ensure_parents(parent: Path) -> None:
        missing: list[Path] = []
        while not parent.exists() and parent != destination:
            missing.append(parent)
            parent = parent.parent
        for item in reversed(missing):
            item.mkdir()
            created_dirs.append(item)

    try:
        with zipfile.ZipFile(path) as archive:
            for info in _checked_infos(archive, limits):
                relative = _safe_name(info.filename)
                target = (destination / Path(*relative.parts)).resolve()
                if target != destination and not target.is_relative_to(destination):
                    raise UnsafeArchive("archive path escaped destination")
                ensure_parents(target.parent)
                if info.is_dir():
                    if not target.exists():
                        target.mkdir()
                        created_dirs.append(target)
                    elif not target.is_dir():
                        raise UnsafeArchive("archive directory collides with a file")
                    continue
                # Exclusive creation both prevents clobbering existing output
                # and refuses final-component symlink collisions.
                with archive.open(info) as source, target.open("xb") as output:
                    extracted.append(target)
                    while chunk := source.read(1024 * 1024):
                        total_written += len(chunk)
                        if total_written > limits.max_uncompressed_bytes:
                            raise UnsafeArchive("archive expansion limit exceeded during extraction")
                        output.write(chunk)
        return extracted
    except BaseException:
        for file in reversed(extracted):
            file.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
