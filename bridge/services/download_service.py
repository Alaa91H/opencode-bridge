"""Managed file downloads for the control panel.

Design constraints that shape this module:

* ``requirements.txt`` is locked to three direct runtime packages by the T38
  supply-chain policy, so this service uses ``httpx`` only and treats an external
  ``yt-dlp`` binary as an optional host tool rather than a dependency.
* The host has about 1GB of RAM, so nothing here transcodes or remuxes: media is
  copied through as-is.
* Downloads are untrusted input. A URL that reaches internal infrastructure must
  be refused before a single byte is fetched, filenames come from the server but
  are still sanitized, and the size is bounded both by a per-file cap and by a
  total budget so a loop cannot fill the disk.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import mimetypes
import os
import re
import shutil
import socket
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

log = logging.getLogger("opencode_bridge.downloads")
UTC = timezone.utc

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")
ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_MAX_FILE_BYTES = 2000 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_TTL_HOURS = 24
CHUNK_BYTES = 1024 * 1024
MAX_FILENAME = 120
DIRECT_TELEGRAM_LIMIT = 50 * 1024 * 1024


class DownloadError(RuntimeError):
    """Raised when a request is refused or a transfer cannot complete."""


class DownloadTooLarge(DownloadError):
    pass


@dataclass(frozen=True)
class DownloadRecord:
    """One stored file, safe to persist and to show in a panel."""

    id: str
    owner_id: str
    filename: str
    path: str
    size_bytes: int
    mime: str
    source_url: str
    created_at: str
    expires_at: str
    direct_send_limit: int

    @property
    def sendable_inline(self) -> bool:
        return self.size_bytes <= self.direct_send_limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "filename": self.filename,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "mime": self.mime,
            "source_url": self.source_url,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "direct_send_limit": self.direct_send_limit,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DownloadRecord":
        return cls(
            id=str(value["id"]),
            owner_id=str(value["owner_id"]),
            filename=str(value["filename"]),
            path=str(value["path"]),
            size_bytes=int(value["size_bytes"]),
            mime=str(value.get("mime", "application/octet-stream")),
            source_url=str(value.get("source_url", "")),
            created_at=str(value["created_at"]),
            expires_at=str(value["expires_at"]),
            direct_send_limit=int(value.get("direct_send_limit", DIRECT_TELEGRAM_LIMIT)),
        )


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(unquote(value or "")).name.strip()
    name = unicodedata.normalize("NFC", name)
    name = SAFE_NAME_RE.sub("_", name).strip("._")
    if not name:
        name = fallback
    return name[:MAX_FILENAME]


def _is_public_host(host: str, *, allow_private: bool = False) -> bool:
    """Refuse anything that is not a routable public address.

    Blocking loopback, private, link-local and reserved ranges is what stops a
    download request from reaching the host itself, the cloud metadata service,
    or another tenant on the network.

    ``allow_private`` exists so tests can point the service at a loopback
    server. It defaults to False and must never be enabled in production; the
    strictness is the product behaviour, not a test convenience.
    """
    if allow_private:
        return True
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # a hostname: resolved separately before connecting
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_url(raw: str, *, allow_private_hosts: bool = False) -> str:
    """Validate scheme and shape, and return a normalized URL."""
    value = (raw or "").strip()
    if not value:
        raise DownloadError("أرسل رابطًا صحيحًا")
    if len(value) > 2048:
        raise DownloadError("الرابط طويل جدًا")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise DownloadError("المسموح بروابط http أو https فقط")
    host = (parsed.hostname or "").strip()
    if not host:
        raise DownloadError("الرابط ما فيه اسم مضيف")
    if not _is_public_host(host, allow_private=allow_private_hosts):
        raise DownloadError("الاستهداف لعناوين داخلية أو محجوزة غير مسموح")
    if not allow_private_hosts and (
        host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".local")
    ):
        raise DownloadError("الاستهداف لعناوين داخلية أو محجوزة غير مسموح")
    return value


async def _assert_resolved_public(url: str, *, allow_private: bool = False) -> None:
    """Re-check every resolved address so a public name cannot point inward.

    A hostname can resolve to a private address, so validating the literal only
    is not enough. The check runs before the client connects, and the redirect
    chain is verified again once the response arrives.
    """
    if allow_private:
        return
    host = urlparse(url).hostname or ""
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DownloadError("تعذر الوصول إلى اسم المضيف") from exc
    if not infos:
        raise DownloadError("تعذر الوصول إلى اسم المضيف")
    for info in infos:
        address = info[4][0]
        if not _is_public_host(str(address)):
            raise DownloadError("الاستهداف لعناوين داخلية أو محجوزة غير مسموح")


class DownloadService:
    """Store owner-scoped downloads under one managed root with a hard budget."""

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        direct_send_limit: int = DIRECT_TELEGRAM_LIMIT,
        timeout_seconds: float = 60.0,
        ytdlp_path: str | None = None,
        allow_private_hosts: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_total_bytes = max(self.max_file_bytes, int(max_total_bytes))
        self.ttl_hours = max(1, int(ttl_hours))
        self.direct_send_limit = max(1, int(direct_send_limit))
        self.timeout_seconds = float(timeout_seconds)
        self.ytdlp_path = ytdlp_path or shutil.which("yt-dlp")
        self.allow_private_hosts = bool(allow_private_hosts)

    # ------------------------------------------------------------- storage

    def ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.root.chmod(0o750)

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _resolve_inside(self, candidate: Path) -> Path:
        resolved = Path(candidate).resolve()
        if resolved != self.root and not resolved.is_relative_to(self.root):
            raise DownloadError("المسار خارج مجلد التنزيلات المُدار")
        return resolved

    def _owner_directory(self, owner_id: str) -> Path:
        directory = self._resolve_inside(self.root / _safe_filename(owner_id, "owner"))
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        directory.chmod(0o750)
        return directory

    # --------------------------------------------------------------- index

    def _read_index(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_index(self, payload: dict[str, dict[str, Any]]) -> None:
        self.ensure_directories()
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.index_path)

    def used_bytes(self) -> int:
        return sum(int(entry.get("size_bytes", 0)) for entry in self._read_index().values())

    def list(self, owner_id: str, *, include_expired: bool = False) -> list[DownloadRecord]:
        now = datetime.now(UTC)
        records: list[DownloadRecord] = []
        for entry in self._read_index().values():
            if str(entry.get("owner_id")) != str(owner_id):
                continue
            try:
                record = DownloadRecord.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                continue
            if not include_expired and _expired(record, now):
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, owner_id: str, download_id: str) -> DownloadRecord | None:
        for record in self.list(owner_id, include_expired=True):
            if record.id == download_id:
                return record
        return None

    # ------------------------------------------------------------ transfer

    async def fetch(self, owner_id: str, url: str, *, filename: str | None = None) -> DownloadRecord:
        """Download one URL into the managed root and register it."""
        self.ensure_directories()
        normalized = validate_url(url, allow_private_hosts=self.allow_private_hosts)
        await _assert_resolved_public(normalized, allow_private=self.allow_private_hosts)

        directory = self._owner_directory(owner_id)
        record_id = f"{int(time.time())}-{abs(hash(normalized)) % 10**8:08d}"
        target = self._resolve_inside(directory / f"{record_id}.part")

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout_seconds, connect=15.0),
            max_redirects=5,
        ) as client:
            request = client.build_request("GET", normalized)
            response = await client.send(request, stream=True)
            try:
                if response.status_code >= 400:
                    raise DownloadError(f"الرابط رجّع حالة {response.status_code}")
                await self._assert_redirects_public(response)
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > self.max_file_bytes:
                    raise DownloadTooLarge("حجم الملف يتجاوز الحد المسموح")
                budget = self.max_total_bytes - self.used_bytes()
                if budget <= 0:
                    raise DownloadTooLarge("مساحة التنزيلات ممتلئة")
                written = await self._stream_to_file(response, target, min(self.max_file_bytes, budget))
            finally:
                await response.aclose()

        mime = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not mime or mime == "application/octet-stream":
            guessed, _ = mimetypes.guess_type(normalized)
            mime = guessed or "application/octet-stream"
        base = filename or Path(unquote(urlparse(normalized).path)).name or "download"
        safe = _safe_filename(base, "download")
        if mime and not Path(safe).suffix:
            suffix = mimetypes.guess_extension(mime) or ""
            safe = f"{safe}{suffix}"[:MAX_FILENAME]
        final = self._resolve_inside(directory / safe)
        if final.exists():
            final = self._resolve_inside(directory / f"{record_id}-{safe}")
        target.replace(final)

        now = datetime.now(UTC)
        record = DownloadRecord(
            id=record_id,
            owner_id=str(owner_id),
            filename=final.name,
            path=str(final),
            size_bytes=written,
            mime=mime,
            source_url=normalized,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=self.ttl_hours)).isoformat(),
            direct_send_limit=self.direct_send_limit,
        )
        index = self._read_index()
        index[record.id] = record.to_dict()
        self._write_index(index)
        return record

    async def _stream_to_file(self, response: httpx.Response, target: Path, budget: int) -> int:
        written = 0
        try:
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    written += len(chunk)
                    if written > budget:
                        raise DownloadTooLarge("حجم الملف يتجاوز الحد المسموح")
                    handle.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return written

    async def _assert_redirects_public(self, response: httpx.Response) -> None:
        """A redirect must not walk the transfer into the internal network."""
        for redirect in response.history:
            await _assert_resolved_public(
                str(redirect.url), allow_private=self.allow_private_hosts
            )

    # -------------------------------------------------------------- removal

    def delete(self, owner_id: str, download_id: str) -> bool:
        record = self.get(owner_id, download_id)
        if record is None:
            return False
        index = self._read_index()
        index.pop(record.id, None)
        self._write_index(index)
        try:
            path = self._resolve_inside(Path(record.path))
            path.unlink(missing_ok=True)
        except (DownloadError, OSError) as exc:
            log.info("تعذر حذف ملف تنزيل: %s", type(exc).__name__)
        self._prune_empty()
        return True

    def cleanup_expired(self, *, now: datetime | None = None) -> dict[str, int]:
        """Remove files past their expiry. Returns counts for the audit log."""
        moment = now or datetime.now(UTC)
        index = self._read_index()
        removed = 0
        freed = 0
        for key, entry in list(index.items()):
            try:
                record = DownloadRecord.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                index.pop(key, None)
                continue
            if not _expired(record, moment):
                continue
            index.pop(key, None)
            freed += record.size_bytes
            try:
                self._resolve_inside(Path(record.path)).unlink(missing_ok=True)
            except (DownloadError, OSError):
                pass
            removed += 1
        if removed:
            self._write_index(index)
            self._prune_empty()
        return {"removed": removed, "freed_bytes": freed}

    def _prune_empty(self) -> None:
        try:
            for child in self.root.iterdir():
                if child.is_dir() and child != self.root:
                    try:
                        child.rmdir()
                    except OSError:
                        pass
        except OSError:
            pass

    # ----------------------------------------------------------- capability

    def capabilities(self) -> dict[str, Any]:
        """What this host can actually do, so the panel never promises more."""
        free = shutil.disk_usage(self.root if self.root.exists() else Path("/")).free
        return {
            "direct_send_limit": self.direct_send_limit,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "ttl_hours": self.ttl_hours,
            "used_bytes": self.used_bytes(),
            "free_bytes": free,
            "ytdlp_available": bool(self.ytdlp_path),
            "ytdlp_path": self.ytdlp_path or "",
            "transcoding": False,
        }


def _expired(record: DownloadRecord, now: datetime) -> bool:
    try:
        expiry = datetime.fromisoformat(record.expires_at)
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry <= now
