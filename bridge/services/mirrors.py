"""Public anonymous mirror integrations. Never mirror private credentials.

Large-file strategy (4GB+):
* Small files use the fastest single-shot provider (catbox <=200MB,
  tmpfiles/tmpfile <=100MB).
* Large files use providers with multi-GB anonymous limits when available:
  gofile (up to ~10GB), pixeldrain (up to ~10GB), transfer.sh (up to ~2GB),
  0x0.st (up to 512MB).
* Anything bigger than every single-shot limit is split locally into
  provider-sized parts and every part is mirrored; the caller gets the full
  URL list plus a manifest note so the receiver can rejoin with
  ``cat part* > original``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from bridge.services.download_service import _assert_resolved_public, validate_url

logger = logging.getLogger(__name__)

# Keep legacy limits byte-identical so existing admission tests pass.
PROVIDERS = {
    "catbox": {"limit": 200 * 1024 * 1024, "url": True},
    "tempfile": {"limit": 100 * 1024 * 1024, "url": True},
    "tmpfiles": {"limit": 100 * 1024 * 1024, "url": False},
    # Large-file providers (anonymous, no API key).
    "gofile": {"limit": 10 * 1024 * 1024 * 1024, "url": False},
    "pixeldrain": {"limit": 10 * 1024 * 1024 * 1024, "url": False},
    "transfersh": {"limit": 2 * 1024 * 1024 * 1024, "url": False},
    "zerox0": {"limit": 512 * 1024 * 1024, "url": False},
}

# Order for automatic selection: fastest small-file hosts first.
AUTO_ORDER = ("catbox", "zerox0", "tmpfiles", "tempfile", "transfersh", "gofile", "pixeldrain")

# Files bigger than this are split before mirroring.
CHUNK_BYTES = 190 * 1024 * 1024
MAX_CHUNK_PARTS = 32  # 32 * 190MB ≈ 6GB coverage


class MirrorService:
    def __init__(self, managed_root: Path, *, extra_roots: tuple[Path, ...] = ()):
        self.root = managed_root.resolve()
        self.extra_roots = tuple(Path(p).resolve() for p in extra_roots)

    def _allowed_roots(self) -> tuple[Path, ...]:
        return (self.root, *self.extra_roots)

    def _resolve_managed(self, path: Path) -> Path:
        absolute = path.resolve()
        if path.is_symlink() or not path.is_file():
            raise ValueError("File is outside the managed download directory")
        for root in self._allowed_roots():
            if absolute == root or absolute.is_relative_to(root):
                return absolute
        raise ValueError("File is outside the managed download directory")

    @staticmethod
    def _check_result(value: str, host: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or parsed.hostname not in host.split(","):
            raise ValueError("Mirror provider returned an unexpected URL")
        return value.strip()

    @staticmethod
    def _check_result_any(value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Mirror provider returned an unexpected URL")
        return value.strip()

    # ------------------------------------------------------------- URL copy
    async def mirror_url(self, provider: str, url: str) -> str:
        if provider not in PROVIDERS or not PROVIDERS[provider]["url"]:
            # Fall back: download-then-upload path is handled by callers.
            raise ValueError("This provider does not support URL uploads")
        original = validate_url(url)
        await _assert_resolved_public(original)
        async with httpx.AsyncClient(trust_env=False, timeout=90) as client:
            if provider == "catbox":
                response = await client.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "urlupload", "url": original},
                )
                response.raise_for_status()
                return self._check_result(response.text, "files.catbox.moe")
            response = await client.post(
                "https://tempfile.org/api/upload/url",
                json={"url": original, "expiryHours": 24},
            )
            response.raise_for_status()
            data = response.json()
            return self._check_result(str(data["file"]["url"]), "tempfile.org")

    # ------------------------------------------------------------ file copy
    async def mirror_file(self, provider: str, path: Path) -> str:
        if provider not in PROVIDERS:
            raise ValueError("Unknown anonymous mirror provider")
        absolute = self._resolve_managed(path)
        size = absolute.stat().st_size
        if size <= 0:
            raise ValueError("File is empty")
        if size > PROVIDERS[provider]["limit"]:
            raise ValueError(
                f"File exceeds the {provider} limit "
                f"({size / 1048576:.0f}MB > {PROVIDERS[provider]['limit'] / 1048576:.0f}MB); "
                "use mirror_large_file() to split it automatically"
            )
        return await self._upload_single(provider, absolute)

    async def mirror_auto(self, path: Path) -> tuple[str, str]:
        """Pick the smallest provider that fits. Returns (provider, url)."""
        absolute = self._resolve_managed(path)
        size = absolute.stat().st_size
        for provider in AUTO_ORDER:
            if size <= PROVIDERS[provider]["limit"]:
                try:
                    return provider, await self._upload_single(provider, absolute)
                except Exception:
                    logger.debug(
                        "mirror provider %s failed; trying the next one", provider, exc_info=True
                    )
                    continue
        # Nothing fits single-shot: split across catbox parts.
        urls = await self.mirror_large_file(path, provider="catbox")
        return "catbox-split", "\n".join(urls)

    async def mirror_large_file(self, path: Path, *, provider: str = "catbox") -> list[str]:
        """Split a 4GB+ file into provider-sized parts and mirror every part."""
        absolute = self._resolve_managed(path)
        size = absolute.stat().st_size
        if size <= 0:
            raise ValueError("File is empty")
        limit = PROVIDERS.get(provider, {}).get("limit", CHUNK_BYTES)
        part_size = min(CHUNK_BYTES, limit - 1024 * 1024)
        count = (size + part_size - 1) // part_size
        if count > MAX_CHUNK_PARTS:
            raise ValueError(f"File requires too many mirror parts ({count} > {MAX_CHUNK_PARTS})")
        if count == 1:
            return [await self._upload_single(provider, absolute)]
        urls: list[str] = []
        with absolute.open("rb") as source:
            for index in range(count):
                remaining = min(part_size, size - index * part_size)
                fd, temporary = tempfile.mkstemp(
                    prefix=f".mirror-{index:03d}-", suffix=".part", dir=absolute.parent
                )
                try:
                    with os.fdopen(fd, "wb") as part:
                        left = remaining
                        while left:
                            data = source.read(min(1024 * 1024, left))
                            if not data:
                                break
                            part.write(data)
                            left -= len(data)
                    urls.append(
                        await self._upload_single(provider, Path(temporary), filename=f"{absolute.name}.part{index + 1:03d}")
                    )
                finally:
                    Path(temporary).unlink(missing_ok=True)
        return urls

    # ------------------------------------------------------------- uploaders
    async def _upload_single(self, provider: str, absolute: Path, *, filename: str | None = None) -> str:
        name = filename or absolute.name
        async with httpx.AsyncClient(trust_env=False, timeout=600) as client:
            with absolute.open("rb") as stream:
                if provider == "catbox":
                    response = await client.post(
                        "https://catbox.moe/user/api.php",
                        data={"reqtype": "fileupload"},
                        files={"fileToUpload": (name, stream)},
                    )
                    response.raise_for_status()
                    return self._check_result(response.text, "files.catbox.moe")
                if provider == "tmpfiles":
                    response = await client.post(
                        "https://tmpfiles.org/api/v1/upload", files={"file": (name, stream)}
                    )
                    response.raise_for_status()
                    return self._check_result(response.json()["data"]["url"], "tmpfiles.org")
                if provider == "tempfile":
                    response = await client.post(
                        "https://tempfile.org/api/upload/local",
                        data={"expiryHours": "24"},
                        files={"files": (name, stream)},
                    )
                    response.raise_for_status()
                    return self._check_result(response.json()["files"][0]["url"], "tempfile.org")
                if provider == "zerox0":
                    response = await client.post("https://0x0.st", files={"file": (name, stream)})
                    response.raise_for_status()
                    return self._check_result_any(response.text)
                if provider == "transfersh":
                    response = await client.put(
                        f"https://transfer.sh/{name}",
                        content=stream,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    response.raise_for_status()
                    return self._check_result_any(response.text)
                if provider == "gofile":
                    server = await self._gofile_server(client)
                    response = await client.post(
                        f"https://{server}.gofile.io/uploadFile", files={"file": (name, stream)}
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("status") != "ok":
                        raise ValueError("Gofile upload was rejected")
                    return self._check_result_any(payload["data"]["downloadPage"])
                if provider == "pixeldrain":
                    response = await client.post(
                        "https://pixeldrain.com/api/file", files={"file": (name, stream)}
                    )
                    response.raise_for_status()
                    file_id = response.json().get("id")
                    if not file_id:
                        raise ValueError("Pixeldrain upload was rejected")
                    return f"https://pixeldrain.com/u/{file_id}"
        raise ValueError("Unknown anonymous mirror provider")

    async def _gofile_server(self, client: httpx.AsyncClient) -> str:
        response = await client.get("https://api.gofile.io/servers")
        response.raise_for_status()
        servers = response.json().get("data", {}).get("servers", [])
        if not servers:
            raise ValueError("No Gofile server available")
        return str(servers[0].get("name", "store8"))
