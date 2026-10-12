"""Offline, read-only validation of secret-inclusive age backup archives.

An age identity (private key) belongs on a separate recovery computer, not
on the production server. This module never writes to the live database or
extracts arbitrary archive paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

ALLOWED_MEMBERS = frozenset({
    "sessions.db",
    ".env",
    "runtime/private/provider-keys.json",
    "runtime/private/host-credentials.json",
    "runtime/memory-policy.json",
    "runtime/auto-update-enabled",
    "runtime/autopilot-mode",
    "runtime/host-auto-update-enabled",
})
BACKUP_NAME = re.compile(r"backup-[0-9]{8}T[0-9]{6}-[a-f0-9]{8}\.age\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_MEMBERS = len(ALLOWED_MEMBERS)


@dataclass(frozen=True)
class RecoveryVerification:
    """Non-sensitive details suitable for a local recovery report."""

    sqlite_integrity: str
    member_count: int
    database_bytes: int
    encrypted_sha256: str


def verify_encrypted_backup(
    encrypted_path: Path,
    identity_path: Path,
    *,
    maximum_bytes: int = MAX_ARCHIVE_BYTES,
) -> RecoveryVerification:
    """Decrypt to private temporary storage; verify archive structure and DB.

    This is for an OFFLINE recovery environment with the private age identity.
    It does not restore the database into a live directory or transmit secrets.
    """
    encrypted_path = Path(encrypted_path)
    identity_path = Path(identity_path)
    if not BACKUP_NAME.fullmatch(encrypted_path.name):
        raise ValueError("Unsupported encrypted backup name")
    metadata = encrypted_path.with_name(encrypted_path.name + ".json")
    for item in (encrypted_path, metadata, identity_path):
        if item.is_symlink() or not item.is_file():
            raise ValueError("Missing or unsafe recovery file")
    if os.name == "posix" and identity_path.stat().st_mode & 0o077:
        raise ValueError("Age identity permissions must be owner-only")
    if not 0 < maximum_bytes <= MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid verification size limit")
    if not 0 < encrypted_path.stat().st_size <= maximum_bytes:
        raise ValueError("Encrypted backup exceeds verification limit")
    if metadata.stat().st_size > 16 * 1024:
        raise ValueError("Backup manifest is too large")
    try:
        info = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Invalid encrypted backup manifest") from exc
    if not isinstance(info, dict) or info.get("encrypted") is not True or info.get("includes_keys") is not True:
        raise ValueError("Untrusted encrypted backup metadata")
    checksum = info.get("sha256")
    if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
        raise ValueError("Missing validated ciphertext hash")

    digest = hashlib.sha256()
    with encrypted_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != checksum:
        raise ValueError("Encrypted backup checksum mismatch")
    age = shutil.which("age")
    if not age:
        raise ValueError("The age decryptor must be installed locally")

    with tempfile.TemporaryDirectory(prefix="opencode-recovery-") as folder:
        private = Path(folder)
        plaintext = private / "recovered.tar.gz"
        try:
            subprocess.run(  # noqa: S603 -- fixed age arguments; no shell or arbitrary executable
                [age, "--decrypt", "--identity", str(identity_path),
                 "--output", str(plaintext), str(encrypted_path)],
                check=True, capture_output=True, timeout=120,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # Do not disclose stderr: the archive could contain secret material.
            raise ValueError("Unable to decrypt backup using supplied identity") from None
        if plaintext.is_symlink() or not plaintext.is_file():
            raise ValueError("Decryptor produced no safe archive")
        # A compressed bomb might be tiny; the member streams are bounded too.
        if plaintext.stat().st_size > maximum_bytes:
            raise ValueError("Plaintext archive exceeds verification limit")
        return _inspect_archive(plaintext, private / "sessions.db", checksum, maximum_bytes)


def _inspect_archive(
    plaintext: Path, database: Path, checksum: str, maximum_bytes: int,
) -> RecoveryVerification:
    seen: set[str] = set()
    total = 0
    database_bytes = 0
    try:
        with tarfile.open(plaintext, mode="r|gz") as bundle:
            for member in bundle:
                if (not member.isfile() or member.name not in ALLOWED_MEMBERS
                        or member.name in seen or len(seen) >= MAX_MEMBERS):
                    raise ValueError("Unsafe, duplicate, or unexpected backup member")
                if member.size < 0 or member.size > maximum_bytes - total:
                    raise ValueError("Decompressed archive exceeds verification limit")
                seen.add(member.name)
                total += member.size
                if member.name == "sessions.db":
                    opened = bundle.extractfile(member)
                    if opened is None:
                        raise ValueError("Cannot read archived database")
                    with database.open("xb") as output, opened:
                        remaining = member.size
                        while remaining:
                            chunk = opened.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("Truncated database archive member")
                            output.write(chunk)
                            remaining -= len(chunk)
                    database_bytes = member.size
                else:
                    # Streaming tar reader advances over unextracted files.
                    continue
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError("Encrypted backup contains an invalid archive") from exc
    if "sessions.db" not in seen or database_bytes == 0:
        raise ValueError("Encrypted backup does not contain a database")
    # mode=ro means a missing or malformed DB must not create a new empty file.
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            row = db.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise ValueError("Restored SQLite integrity verification failed")
    except sqlite3.Error as exc:
        raise ValueError("Archived database is not valid SQLite") from exc
    return RecoveryVerification("ok", len(seen), database_bytes, checksum)
