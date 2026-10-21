"""GitHub Releases management for OpenCode Bridge.

Handles release creation, publishing, asset upload, and tracking
via the GitHub REST API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
)

log = logging.getLogger("opencode_bridge.github.releases")


@dataclass
class GitHubRelease:
    """Information about a GitHub Release."""

    id: int
    tag_name: str
    name: str | None
    body: str | None
    state: str  # open, draft, prerelease, released
    html_url: str
    upload_url: str
    author: str
    created_at: str
    published_at: str | None
    draft: bool
    prerelease: bool
    assets: list[dict[str, Any]] = None


@dataclass
class GitHubReleaseAsset:
    """Information about a GitHub Release Asset."""

    id: int
    name: str
    label: str | None
    url: str
    browser_download_url: str
    size: int
    content_type: str
    download_count: int
    created_at: str
    uploader: str | None


class GitHubReleaseManager:
    """Manages GitHub Releases for OpenCode Bridge.

    Provides operations for creating, publishing, and managing
    releases and their assets through the Telegram bridge.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client

    async def create_release(
        self,
        owner: str,
        repo: str,
        tag_name: str,
        name: str | None = None,
        body: str | None = None,
        draft: bool = False,
        prerelease: bool = False,
        target_commitish: str | None = None,
        generate_notes: bool = False,
    ) -> GitHubRelease:
        """Create a new GitHub Release.

        Args:
            owner: Repository owner.
            repo: Repository name.
            tag_name: Tag name for the release.
            name: Release title.
            body: Release notes.
            draft: Whether to create as draft.
            prerelease: Whether to mark as prerelease.
            target_commitish: Branch or commit SHA to tag.
            generate_notes: Whether to auto-generate release notes.

        Returns:
            GitHubRelease with details of the created release.
        """
        body_dict: dict[str, Any] = {
            "tag_name": tag_name,
            "draft": draft,
            "prerelease": prerelease,
            "generate_release_notes": generate_notes,
        }
        if name is not None:
            body_dict["name"] = name
        if body is not None:
            body_dict["body"] = body
        if target_commitish:
            body_dict["target_commitish"] = target_commitish

        try:
            data = await self.client.post(f"/repos/{owner}/{repo}/releases", json_body=body_dict)
        except GitHubAPIError as exc:
            log.error("فشل إنشاء إصدار في %s/%s: %s", owner, repo, exc)
            raise

        return self._parse_release(data)

    async def get_release(self, owner: str, repo: str, release_id: int) -> GitHubRelease:
        """Get information about a specific Release."""
        data = await self.client.get(f"/repos/{owner}/{repo}/releases/{release_id}")
        return self._parse_release(data)

    async def get_release_by_tag(self, owner: str, repo: str, tag: str) -> GitHubRelease:
        """Get a Release by its tag name."""
        data = await self.client.get(f"/repos/{owner}/{repo}/releases/tags/{tag}")
        return self._parse_release(data)

    async def list_releases(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int = 100,
    ) -> list[GitHubRelease]:
        """List Releases for a repository."""
        data = await self.client.get(f"/repos/{owner}/{repo}/releases", params={"per_page": per_page})
        return [self._parse_release(item) for item in data] if isinstance(data, list) else []

    async def publish_release(self, owner: str, repo: str, release_id: int) -> GitHubRelease:
        """Publish a draft Release."""
        data = await self.client.patch(
            f"/repos/{owner}/{repo}/releases/{release_id}",
            json_body={"draft": False},
        )
        return self._parse_release(data)

    async def delete_release(self, owner: str, repo: str, release_id: int) -> bool:
        """Delete a Release and its associated tag."""
        await self.client.delete(f"/repos/{owner}/{repo}/releases/{release_id}")
        return True

    async def upload_release_asset(
        self,
        owner: str,
        repo: str,
        release_id: int,
        file_path: Path,
        *,
        content_type: str = "application/octet-stream",
        label: str | None = None,
    ) -> GitHubReleaseAsset:
        """Upload an asset to a Release.

        Args:
            owner: Repository owner.
            repo: Repository name.
            release_id: Release ID.
            file_path: Path to the file to upload.
            content_type: MIME type of the file.
            label: Optional label for the asset.

        Returns:
            GitHubReleaseAsset with upload details.
        """

        file_data = file_path.read_bytes()
        filename = file_path.name

        # GitHub asset upload uses a different URL pattern
        upload_url = f"https://uploads.github.com/repos/{owner}/{repo}/releases/{release_id}/assets"
        params: dict[str, Any] = {"name": filename}
        if label:
            params["label"] = label

        headers = {
            "Authorization": f"Bearer {self.client.config.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": content_type,
            "Content-Length": str(len(file_data)),
            "User-Agent": self.client.config.user_agent,
        }

        async with httpx.AsyncClient(timeout=self.client.config.timeout) as http_client:
            response = await http_client.post(
                upload_url,
                params=params,
                headers=headers,
                content=file_data,
            )

        if response.status_code == 401:
            from bridge.services.github.api import GitHubAuthenticationError
            raise GitHubAuthenticationError("فشل المصادقة مع GitHub أثناء رفع الملف", status_code=401)
        if response.status_code == 403:
            from bridge.services.github.api import GitHubPermissionError
            raise GitHubPermissionError("لا تملك صلاحية رفع الملفات للإصدار", status_code=403)
        if response.status_code >= 400:
            from bridge.services.github.api import GitHubAPIError
            raise GitHubAPIError(f"فشل رفع الملف: رمز الحالة {response.status_code}", status_code=response.status_code)

        data = response.json()
        return self._parse_asset(data)

    async def list_release_assets(
        self,
        owner: str,
        repo: str,
        release_id: int,
    ) -> list[GitHubReleaseAsset]:
        """List assets for a Release."""
        data = await self.client.get(f"/repos/{owner}/{repo}/releases/{release_id}/assets")
        return [self._parse_asset(item) for item in data] if isinstance(data, list) else []

    async def delete_release_asset(
        self,
        owner: str,
        repo: str,
        asset_id: int,
    ) -> bool:
        """Delete a Release Asset."""
        await self.client.delete(f"/repos/{owner}/{repo}/releases/assets/{asset_id}")
        return True

    async def edit_release(
        self,
        owner: str,
        repo: str,
        release_id: int,
        *,
        name: str | None = None,
        body: str | None = None,
        draft: bool | None = None,
        prerelease: bool | None = None,
    ) -> GitHubRelease:
        """Edit a Release's metadata."""
        body_dict: dict[str, Any] = {}
        if name is not None:
            body_dict["name"] = name
        if body is not None:
            body_dict["body"] = body
        if draft is not None:
            body_dict["draft"] = draft
        if prerelease is not None:
            body_dict["prerelease"] = prerelease

        data = await self.client.patch(
            f"/repos/{owner}/{repo}/releases/{release_id}",
            json_body=body_dict,
        )
        return self._parse_release(data)

    async def get_latest_release(self, owner: str, repo: str) -> GitHubRelease:
        """Get the latest Release for a repository."""
        data = await self.client.get(f"/repos/{owner}/{repo}/releases/latest")
        return self._parse_release(data)

    def _parse_release(self, data: dict[str, Any]) -> GitHubRelease:
        """Convert raw API response to GitHubRelease dataclass."""
        author = data.get("author", {})
        assets = data.get("assets", [])

        return GitHubRelease(
            id=data.get("id", 0),
            tag_name=data.get("tag_name", ""),
            name=data.get("name"),
            body=data.get("body"),
            state="draft" if data.get("draft") else ("prerelease" if data.get("prerelease") else "released"),
            html_url=data.get("html_url", ""),
            upload_url=data.get("upload_url", ""),
            author=author.get("login", ""),
            created_at=data.get("created_at", ""),
            published_at=data.get("published_at"),
            draft=data.get("draft", False),
            prerelease=data.get("prerelease", False),
            assets=[self._parse_asset(a) for a in assets] if isinstance(assets, list) else [],
        )

    def _parse_asset(self, data: dict[str, Any]) -> GitHubReleaseAsset:
        """Convert raw API response to GitHubReleaseAsset dataclass."""
        uploader = data.get("uploader", {})
        return GitHubReleaseAsset(
            id=data.get("id", 0),
            name=data.get("name", ""),
            label=data.get("label"),
            url=data.get("url", ""),
            browser_download_url=data.get("browser_download_url", ""),
            size=data.get("size", 0),
            content_type=data.get("content_type", ""),
            download_count=data.get("download_count", 0),
            created_at=data.get("created_at", ""),
            uploader=uploader.get("login") if isinstance(uploader, dict) else None,
        )
