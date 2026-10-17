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
import stat
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from bridge.services.pinned_public_transport import PublicOnlyHTTPTransport

log = logging.getLogger("opencode_bridge.downloads")
UTC = UTC

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")
ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 40 * 1024 * 1024 * 1024
DEFAULT_TTL_HOURS = 24
CHUNK_BYTES = 1024 * 1024
MAX_FILENAME = 120
DIRECT_TELEGRAM_LIMIT = 50 * 1024 * 1024


class DownloadError(RuntimeError):
    """Raised when a request is refused or a transfer cannot complete."""


class DownloadTooLarge(DownloadError):
    pass


class DownloadBusy(DownloadError):
    """Another download or cleanup currently owns the shared storage lock."""


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
    def from_dict(cls, value: dict[str, Any]) -> DownloadRecord:
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
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DownloadError("الرابط يحتوي على محارف تحكم غير مسموحة")
    try:
        parsed = urlparse(value)
        port = parsed.port  # Reject malformed ports before making any request.
    except ValueError as exc:
        raise DownloadError("منفذ أو عنوان رابط غير صحيح") from exc
    if parsed.username is not None or parsed.password is not None:
        raise DownloadError("الروابط التي تحتوي على بيانات دخول غير مسموحة")
    if port is not None and port == 0:
        raise DownloadError("منفذ غير صحيح")
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise DownloadError("المسموح بروابط http أو https فقط")
    try:
        host = (parsed.hostname or "").strip()
    except ValueError as exc:
        raise DownloadError("عنوان مضيف غير صحيح") from exc
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
        self.max_total_bytes = max(1, min(DEFAULT_MAX_TOTAL_BYTES, int(max_total_bytes)))
        self.max_file_bytes = max(1, min(self.max_total_bytes, int(max_file_bytes)))
        self.ttl_hours = max(1, min(DEFAULT_TTL_HOURS, int(ttl_hours)))
        self.direct_send_limit = max(1, int(direct_send_limit))
        self.timeout_seconds = float(timeout_seconds)
        self.ytdlp_path = ytdlp_path or shutil.which("yt-dlp")
        self.allow_private_hosts = bool(allow_private_hosts)
        # Serialize state-changing downloads so concurrent requests cannot both
        # spend the same remaining disk budget or overwrite index changes.
        self._transfer_lock = asyncio.Lock()

    def _acquire_file_lock(self) -> int:
        """Nonblocking OS lock shared by multiple Bridge processes.

        A dead process releases its operating-system lock automatically.
        Never trust a preexisting symlink, FIFO or multiply-linked lock file.
        """
        self.ensure_directories()
        path = self.root / ".downloads.lock"
        if path.is_symlink():
            raise DownloadError("Unsafe shared download lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError:
            raise DownloadError("Cannot open shared download lock") from None
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise DownloadError("Unsafe shared download lock")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except Exception as exc:
            os.close(fd)
            if isinstance(exc, BlockingIOError):
                raise DownloadBusy("Managed download storage is busy") from None
            if isinstance(exc, DownloadError):
                raise
            raise DownloadError("Shared download lock is unavailable") from None

    @staticmethod
    def _release_file_lock(fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            # POSIX flock is released when the file descriptor is closed.
            os.close(fd)

    @contextmanager
    def _storage_guard(self):
        fd = self._acquire_file_lock()
        try:
            yield
        finally:
            self._release_file_lock(fd)

    async def _acquire_file_lock_async(self) -> int:
        # Do not block the event loop while another process downloads.
        for attempt in range(100):
            try:
                return self._acquire_file_lock()
            except DownloadBusy:
                if attempt == 99:
                    raise
                await asyncio.sleep(0.1)
        raise DownloadBusy("Managed download storage is busy")

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
        """Never mistake an unreadable or tampered index for empty storage.

        Treating corruption as an empty index loses ownership/expiry records
        and allows quota overcommit while previously stored files remain.
        Only a genuinely missing index is an empty new download store.
        """
        if self.index_path.is_symlink():
            raise DownloadError("Unsafe download index; recovery required")
        try:
            if self.index_path.stat().st_size > 8 * 1024 * 1024:
                raise DownloadError("Download index exceeds safe size limit")
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError) as exc:
            if isinstance(exc, DownloadError):
                raise
            raise DownloadError("Unreadable download index; recovery required") from None
        if not isinstance(payload, dict):
            raise DownloadError("Invalid download index; recovery required")
        for key, entry in payload.items():
            try:
                if not isinstance(key, str) or not isinstance(entry, dict):
                    raise ValueError("malformed index entry")
                record = DownloadRecord.from_dict(entry)
                if (record.id != key or record.size_bytes <= 0
                        or not re.fullmatch(r"[0-9a-f]{32}", key)):
                    raise ValueError("invalid download identity or size")
                stored = Path(record.path)
                if (not stored.is_absolute() or stored.is_symlink()
                        or not stored.resolve().is_relative_to(self.root)
                        or not stored.is_file()
                        or stored.stat().st_size != record.size_bytes):
                    raise ValueError("download file no longer matches its record")
                expiry = datetime.fromisoformat(record.expires_at)
                if expiry.tzinfo is None:
                    raise ValueError("missing download expiry timezone")
            except (KeyError, TypeError, ValueError, OSError, OverflowError, RuntimeError):
                raise DownloadError("Unsafe download index entry; recovery required") from None
        return payload

    def _write_index(self, payload: dict[str, dict[str, Any]]) -> None:
        self.ensure_directories()
        fd, name = tempfile.mkstemp(prefix=".index-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.index_path)
        finally:
            Path(name).unlink(missing_ok=True)

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

    async def fetch(self, owner_id: str, url: str, *, filename: str | None = None,
                    media: bool = False) -> DownloadRecord:
        """Queue media (yt-dlp) or direct HTTP under a common 20 GiB quota."""
        async with self._transfer_lock:
            lock_fd = await self._acquire_file_lock_async()
            try:
                if media:
                    return await self._fetch_media_locked(owner_id, url)
                return await self._fetch_locked(owner_id, url, filename=filename)
            finally:
                self._release_file_lock(lock_fd)

    async def _fetch_media_locked(self, owner_id: str, url: str) -> DownloadRecord:
        """Run a bounded yt-dlp extractor without executing downloaded content.

        This needs outbound network filtering on the host for complete SSRF
        isolation: video extractors can contact other domains after URL checks.
        """
        if not self.ytdlp_path:
            raise DownloadError("yt-dlp is not installed on this server")
        normalized = validate_url(url, allow_private_hosts=self.allow_private_hosts)
        await _assert_resolved_public(normalized, allow_private=self.allow_private_hosts)
        self.ensure_directories()
        owner_dir = self._owner_directory(owner_id)
        budget = min(self.max_file_bytes, self.max_total_bytes - self.used_bytes())
        free = shutil.disk_usage(self.root).free
        budget = min(budget, max(0, free - 256 * 1024 * 1024))
        if budget <= 0:
            raise DownloadTooLarge("Managed download storage is full")
        download_id = uuid.uuid4().hex
        work = self._resolve_inside(owner_dir / (download_id + ".working"))
        work.mkdir(mode=0o700)
        command = [
            self.ytdlp_path, "--ignore-config", "--no-playlist",
            "--no-progress", "--no-warnings", "--no-overwrites",
            "--restrict-filenames", "--socket-timeout", "15",
            "--retries", "2", "--fragment-retries", "2",
            "--max-filesize", str(budget),
            "--format", "b/bv*+ba",
            "--output", "%(title).80B.%(ext)s",
            normalized,
        ]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=work, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 900
            while True:
                try:
                    # 2s cadence: budget overshoot is re-checked on completion,
                    # so a slower poll only trades wakeups, never safety.
                    exit_code = await asyncio.wait_for(process.wait(), timeout=2.0)
                    break
                except TimeoutError:
                    pass
                size = sum(item.stat().st_size for item in work.rglob("*") if item.is_file())
                if size > budget:
                    raise DownloadTooLarge("Media download exceeds the file or storage quota")
                if loop.time() >= deadline:
                    raise DownloadError("Media downloader exceeded time limit")
            if exit_code:
                raise DownloadError("Media extractor could not download this URL")
            outputs = [item for item in work.iterdir() if item.is_file()
                       and item.suffix not in {".part", ".ytdl", ".temp"}]
            if len(outputs) != 1 or not outputs[0].is_file() or outputs[0].is_symlink():
                raise DownloadError("Media extractor did not produce a single file")
            result = outputs[0]
            size = result.stat().st_size
            if not 0 < size <= budget:
                raise DownloadTooLarge("Media output exceeds permitted size")
            name = _safe_filename(result.name, download_id)
            destination = self._resolve_inside(owner_dir / (download_id + "-" + name))
            result.replace(destination)
            mime = mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
            now = datetime.now(UTC)
            record = DownloadRecord(
                id=download_id, owner_id=str(owner_id), filename=destination.name,
                path=str(destination), size_bytes=size, mime=mime,
                source_url=normalized, created_at=now.isoformat(),
                expires_at=(now + timedelta(hours=self.ttl_hours)).isoformat(),
                direct_send_limit=self.direct_send_limit)
            try:
                index = self._read_index()
                index[download_id] = record.to_dict()
                self._write_index(index)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return record
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            shutil.rmtree(work, ignore_errors=True)

    async def _request_public(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        """Validate each redirect *before* opening its network connection.

        This prevents the previous unsafe sequence where httpx followed a
        redirect to a private host and only then inspected response.history.
        The hostname lookup and the actual TCP dial are still distinct; a
        network egress firewall is needed for complete DNS-rebinding defense.
        """
        current = url
        for hop in range(6):
            current = validate_url(current, allow_private_hosts=self.allow_private_hosts)
            await _assert_resolved_public(current, allow_private=self.allow_private_hosts)
            response = await client.send(client.build_request("GET", current), stream=True)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("Location")
            next_url = str(response.url.join(location)) if location else None
            await response.aclose()
            if not next_url:
                raise DownloadError("إعادة توجيه بدون عنوان صالح")
            if hop == 5:
                raise DownloadError("تجاوز الرابط الحد المسموح لإعادات التوجيه")
            current = next_url
        raise DownloadError("تجاوز الرابط الحد المسموح لإعادات التوجيه")

    async def _fetch_locked(self, owner_id: str, url: str, *, filename: str | None) -> DownloadRecord:
        self.ensure_directories()
        self._read_index()  # Fail closed before opening untrusted network connections.
        normalized = validate_url(url, allow_private_hosts=self.allow_private_hosts)
        directory = self._owner_directory(owner_id)
        record_id = uuid.uuid4().hex
        target = self._resolve_inside(directory / f"{record_id}.part")

        client_options: dict[str, Any] = {
            "follow_redirects": False,
            "trust_env": False,  # Never honor a host environment proxy for untrusted URLs.
            "timeout": httpx.Timeout(self.timeout_seconds, connect=15.0),
        }
        if not self.allow_private_hosts:
            # Validate DNS at the socket boundary and dial that exact numeric
            # public IP. A pre-connect DNS check alone is not a rebinding fix.
            client_options["transport"] = PublicOnlyHTTPTransport()
        async with httpx.AsyncClient(**client_options) as client:
            response = await self._request_public(client, normalized)
            try:
                if response.status_code >= 400:
                    raise DownloadError(f"الرابط رجّع حالة {response.status_code}")
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
        try:
            index = self._read_index()
            index[record.id] = record.to_dict()
            self._write_index(index)
        except BaseException:
            final.unlink(missing_ok=True)
            raise
        return record

    async def _stream_to_file(self, response: httpx.Response, target: Path, budget: int) -> int:
        written = 0
        try:
            with target.open("xb") as handle:
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    written += len(chunk)
                    if written > budget:
                        raise DownloadTooLarge("حجم الملف يتجاوز الحد المسموح")
                    handle.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return written

    # -------------------------------------------------------------- removal

    def delete(self, owner_id: str, download_id: str) -> bool:
        with self._storage_guard():
            return self._delete_locked(owner_id, download_id)

    def _delete_locked(self, owner_id: str, download_id: str) -> bool:
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

    def cleanup_expired(
        self, *, now: datetime | None = None, previous_utc_day: bool = False,
    ) -> dict[str, int]:
        with self._storage_guard():
            return self._cleanup_expired_locked(now=now, previous_utc_day=previous_utc_day)

    def _cleanup_expired_locked(
        self, *, now: datetime | None = None, previous_utc_day: bool = False,
    ) -> dict[str, int]:
        """Retire TTL-expired files and, optionally, files from prior UTC days.

        Commit index changes before removing any file. A failed disk write
        cannot leave dangling index references to already-deleted files.
        """
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            raise ValueError("Cleanup requires a timezone-aware clock")
        midnight = moment.astimezone(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        index = self._read_index()
        retired: list[DownloadRecord] = []
        freed = 0
        for key, entry in list(index.items()):
            record = DownloadRecord.from_dict(entry)
            created = datetime.fromisoformat(record.created_at)
            if created.tzinfo is None:
                raise DownloadError("Download creation date is missing its timezone")
            before_midnight = previous_utc_day and created.astimezone(UTC) < midnight
            if not (_expired(record, moment) or before_midnight):
                continue
            index.pop(key)
            retired.append(record)
            freed += record.size_bytes
        if retired:
            self._write_index(index)
            for record in retired:
                try:
                    self._resolve_inside(Path(record.path)).unlink(missing_ok=True)
                except (DownloadError, OSError):
                    # Retain orphaned data for diagnosis rather than hide errors.
                    pass
            self._prune_empty()
        return {"removed": len(retired), "freed_bytes": freed}

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
