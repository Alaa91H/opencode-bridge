"""Public anonymous mirror integrations. Never mirror private credentials."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from bridge.services.download_service import _assert_resolved_public, validate_url

PROVIDERS = {
    "catbox": {"limit": 200 * 1024 * 1024, "url": True},
    "tempfile": {"limit": 100 * 1024 * 1024, "url": True},
    "tmpfiles": {"limit": 100 * 1024 * 1024, "url": False},
}


class MirrorService:
    def __init__(self, managed_root: Path):
        self.root = managed_root.resolve()

    @staticmethod
    def _check_result(value: str, host: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or parsed.hostname not in host.split(","):
            raise ValueError("Mirror provider returned an unexpected URL")
        return value.strip()

    async def mirror_url(self, provider: str, url: str) -> str:
        if provider not in PROVIDERS or not PROVIDERS[provider]["url"]:
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

    async def mirror_file(self, provider: str, path: Path) -> str:
        if provider not in PROVIDERS:
            raise ValueError("Unknown anonymous mirror provider")
        absolute = path.resolve()
        if not absolute.is_relative_to(self.root) or path.is_symlink() or not path.is_file():
            raise ValueError("File is outside the managed download directory")
        size = absolute.stat().st_size
        if size <= 0 or size > PROVIDERS[provider]["limit"]:
            raise ValueError("File exceeds the mirror provider limit")
        async with httpx.AsyncClient(trust_env=False, timeout=180) as client:
            with absolute.open("rb") as stream:
                files = {"fileToUpload" if provider == "catbox" else
                         "file" if provider == "tmpfiles" else "files":
                         (absolute.name, stream)}
                if provider == "catbox":
                    response = await client.post(
                        "https://catbox.moe/user/api.php",
                        data={"reqtype": "fileupload"}, files=files)
                    response.raise_for_status()
                    return self._check_result(response.text, "files.catbox.moe")
                if provider == "tmpfiles":
                    response = await client.post("https://tmpfiles.org/api/v1/upload",
                                                 files=files)
                    response.raise_for_status()
                    link = response.json()["data"]["url"]
                    return self._check_result(link, "tmpfiles.org")
                response = await client.post("https://tempfile.org/api/upload/local",
                                             data={"expiryHours": "24"}, files=files)
                response.raise_for_status()
                link = response.json()["files"][0]["url"]
                return self._check_result(link, "tempfile.org")
