"""GitHub repository management for OpenCode Bridge.

Handles repository creation, cloning, configuration, and metadata
operations via the GitHub REST API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
)

log = logging.getLogger("opencode_bridge.github.repositories")


@dataclass
class GitHubRepoConfig:
    """Configuration for creating or configuring a GitHub repository."""

    name: str
    description: str | None = None
    private: bool = True
    auto_init: bool = False
    gitignore_template: str | None = None
    license_template: str | None = None
    allow_squash_merge: bool = True
    allow_merge_commit: bool = True
    allow_rebase_merge: bool = True
    delete_branch_on_merge: bool = True
    has_issues: bool = True
    has_projects: bool = True
    has_wiki: bool = False


@dataclass
class GitHubRepoInfo:
    """Information about a GitHub repository."""

    id: int
    name: str
    full_name: str
    owner: str
    html_url: str
    clone_url: str
    ssh_url: str
    private: bool
    description: str | None
    default_branch: str
    created_at: str
    updated_at: str
    pushed_at: str | None
    size: int
    stargazers_count: int
    watchers_count: int
    forks_count: int
    open_issues_count: int
    topics: list[str] = None
    license: dict[str, Any] | None = None


class GitHubRepositoryManager:
    """Manages GitHub repositories for OpenCode Bridge users.

    Provides high-level operations for creating, configuring, and
    interacting with GitHub repositories through the Telegram bridge.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client

    async def create_repository(self, config: GitHubRepoConfig) -> GitHubRepoInfo:
        """Create a new GitHub repository for the authenticated user.

        Args:
            config: Repository configuration including name, visibility, etc.

        Returns:
            GitHubRepoInfo with details of the created repository.

        Raises:
            GitHubAuthenticationError: If not authenticated.
            GitHubPermissionError: If lacking permission to create repos.
            GitHubAPIError: For other API errors.
        """
        body: dict[str, Any] = {
            "name": config.name,
            "private": config.private,
            "auto_init": config.auto_init,
            "allow_squash_merge": config.allow_squash_merge,
            "allow_merge_commit": config.allow_merge_commit,
            "allow_rebase_merge": config.allow_rebase_merge,
            "delete_branch_on_merge": config.delete_branch_on_merge,
            "has_issues": config.has_issues,
            "has_projects": config.has_projects,
            "has_wiki": config.has_wiki,
        }

        if config.description:
            body["description"] = config.description
        if config.gitignore_template:
            body["gitignore_template"] = config.gitignore_template
        if config.license_template:
            body["license_template"] = config.license_template

        try:
            data = await self.client.post("/user/repos", json_body=body)
        except GitHubAPIError as exc:
            log.error("فشل إنشاء مستودع GitHub %s: %s", config.name, exc)
            raise

        return self._parse_repo_info(data)

    async def get_repository(self, owner: str, repo: str) -> GitHubRepoInfo:
        """Get information about a specific repository.

        Args:
            owner: Repository owner (user or organization).
            repo: Repository name.

        Returns:
            GitHubRepoInfo with repository details.

        Raises:
            GitHubNotFoundError: If repository not found.
        """
        data = await self.client.get(f"/repos/{owner}/{repo}")
        return self._parse_repo_info(data)

    async def list_repositories(
        self,
        *,
        affiliation: str = "owner,collaborator,organization_member",
        sort: str = "updated",
        direction: str = "desc",
        per_page: int = 100,
    ) -> list[GitHubRepoInfo]:
        """List repositories accessible to the authenticated user.

        Returns:
            List of GitHubRepoInfo objects.
        """
        data = await self.client.get_user_repos(
            affiliation=affiliation,
            sort=sort,
            direction=direction,
            per_page=per_page,
        )
        return [self._parse_repo_info(item) for item in data]

    async def delete_repository(self, owner: str, repo: str) -> bool:
        """Delete a GitHub repository.

        Warning: This is irreversible. All data will be lost.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            True if deletion succeeded.
        """
        await self.client.delete(f"/repos/{owner}/{repo}")
        return True

    async def get_repository_contents(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get contents of a directory or file in a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: Path within the repository (empty for root).
            ref: Branch/tag/commit SHA (defaults to default branch).

        Returns:
            List of content items (files and directories).
        """
        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref
        return await self.client.get(f"/repos/{owner}/{repo}/contents/{path}", params=params)

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> str | None:
        """Get the decoded content of a file in a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: Path to the file.
            ref: Branch/tag/commit SHA.

        Returns:
            Decoded file content, or None if not found.
        """
        import base64

        params: dict[str, Any] = {}
        if ref:
            params["ref"] = ref

        data = await self.client.get(f"/repos/{owner}/{repo}/contents/{path}", params=params)

        if isinstance(data, list):
            return None  # It's a directory, not a file

        content_b64 = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if encoding == "base64":
            try:
                return base64.b64decode(content_b64).decode("utf-8")
            except Exception as exc:
                log.warning("فشل فك ترميز محتوى ملف %s: %s", path, exc)
                return None
        return content_b64

    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a file in a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: File path within the repository.
            content: File content (will be base64 encoded automatically).
            message: Commit message.
            branch: Target branch (defaults to default branch).
            sha: Blob SHA of the file being replaced (required for updates).

        Returns:
            API response with commit details.

        Raises:
            GitHubPermissionError: If lacking write permission.
        """
        import base64

        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if branch:
            body["branch"] = branch
        if sha:
            body["sha"] = sha

        return await self.client.put(f"/repos/{owner}/{repo}/contents/{path}", json_body=body)

    async def delete_file(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        sha: str,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Delete a file from a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            path: File path to delete.
            message: Commit message.
            sha: Blob SHA of the file to delete.
            branch: Target branch.

        Returns:
            API response with commit details.
        """
        body: dict[str, Any] = {
            "message": message,
            "sha": sha,
        }
        if branch:
            body["branch"] = branch

        return await self.client.delete(f"/repos/{owner}/{repo}/contents/{path}", json_body=body)

    async def get_branches(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List branches in a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            List of branch objects.
        """
        return await self.client.get(f"/repos/{owner}/{repo}/branches")

    async def get_default_branch(self, owner: str, repo: str) -> str:
        """Get the default branch name of a repository."""
        repo_info = await self.get_repository(owner, repo)
        return repo_info.default_branch

    async def enable_issues(self, owner: str, repo: str, enabled: bool = True) -> dict[str, Any]:
        """Enable or disable Issues for a repository."""
        return await self.client.patch(
            f"/repos/{owner}/{repo}",
            json_body={"has_issues": enabled},
        )

    async def enable_wiki(self, owner: str, repo: str, enabled: bool = True) -> dict[str, Any]:
        """Enable or disable Wiki for a repository."""
        return await self.client.patch(
            f"/repos/{owner}/{repo}",
            json_body={"has_wiki": enabled},
        )

    async def set_repository_description(
        self,
        owner: str,
        repo: str,
        description: str | None,
    ) -> dict[str, Any]:
        """Set or update repository description."""
        return await self.client.patch(
            f"/repos/{owner}/{repo}",
            json_body={"description": description},
        )

    async def add_repository_topics(
        self,
        owner: str,
        repo: str,
        topics: list[str],
    ) -> list[str]:
        """Add topics to a repository."""
        data = await self.client.put(
            f"/repos/{owner}/{repo}/topics",
            json_body={"names": topics},
        )
        return data.get("names", []) if isinstance(data, dict) else []

    async def get_repository_topics(self, owner: str, repo: str) -> list[str]:
        """Get topics for a repository."""
        data = await self.client.get(f"/repos/{owner}/{repo}/topics")
        return data.get("names", []) if isinstance(data, dict) else []

    def _parse_repo_info(self, data: dict[str, Any]) -> GitHubRepoInfo:
        """Convert raw API response to GitHubRepoInfo dataclass."""
        owner_data = data.get("owner", {})
        return GitHubRepoInfo(
            id=data.get("id", 0),
            name=data.get("name", ""),
            full_name=data.get("full_name", ""),
            owner=owner_data.get("login", ""),
            html_url=data.get("html_url", ""),
            clone_url=data.get("clone_url", ""),
            ssh_url=data.get("ssh_url", ""),
            private=data.get("private", False),
            description=data.get("description"),
            default_branch=data.get("default_branch", "main"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            pushed_at=data.get("pushed_at"),
            size=data.get("size", 0),
            stargazers_count=data.get("stargazers_count", 0),
            watchers_count=data.get("watchers_count", 0),
            forks_count=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            topics=data.get("topics", []),
            license=data.get("license"),
        )


class LocalRepositoryManager:
    """Manages local git repositories for OpenCode Bridge workspace operations.

    Provides safe local git operations for repositories cloned from GitHub,
    with workspace isolation and safety guards.
    """

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd() / "github-workspaces"
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def get_workspace_path(self, owner: str, repo: str) -> Path:
        """Get the local workspace path for a repository."""
        return self.workspace_root / owner / repo

    def workspace_exists(self, owner: str, repo: str) -> bool:
        """Check if a workspace exists locally."""
        return self.get_workspace_path(owner, repo).is_dir()

    def list_workspaces(self) -> list[tuple[str, str, Path]]:
        """List all existing local workspaces."""
        workspaces: list[tuple[str, str, Path]] = []
        if not self.workspace_root.exists():
            return workspaces
        for owner_dir in self.workspace_root.iterdir():
            if owner_dir.is_dir():
                for repo_dir in owner_dir.iterdir():
                    if repo_dir.is_dir() and (repo_dir / ".git").exists():
                        workspaces.append((owner_dir.name, repo_dir.name, repo_dir))
        return workspaces

    async def clone_repository(
        self,
        clone_url: str,
        owner: str,
        repo: str,
        *,
        branch: str | None = None,
        depth: int = 1,
    ) -> Path:
        """Clone a GitHub repository to the local workspace.

        Args:
            clone_url: HTTPS clone URL from GitHub.
            owner: Repository owner.
            repo: Repository name.
            branch: Specific branch to clone.
            depth: Clone depth (1 for shallow clone).

        Returns:
            Path to the cloned workspace.
        """

        workspace_path = self.get_workspace_path(owner, repo)

        if workspace_path.exists() and (workspace_path / ".git").exists():
            log.warning("مساحة العمل موجودة بالفعل: %s", workspace_path)
            return workspace_path

        workspace_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["git", "clone"]
        if depth:
            cmd.extend(["--depth", str(depth)])
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([clone_url, str(workspace_path)])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, _stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = _stderr.decode("utf-8", errors="replace") if _stderr else "خطأ غير معروف"
            raise RuntimeError(f"فشل استنساخ المستودع: {error_msg}")

        log.info("تم استنساخ المستودع %s/%s إلى %s", owner, repo, workspace_path)
        return workspace_path

    async def pull_repository(self, owner: str, repo: str) -> str:
        """Pull latest changes for a local repository.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            Output from git pull command.
        """

        workspace_path = self.get_workspace_path(owner, repo)
        if not workspace_path.exists():
            raise FileNotFoundError(f"مساحة العمل غير موجودة: {workspace_path}")

        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace_path), "pull",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, _stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = _stderr.decode("utf-8", errors="replace") if _stderr else "خطأ غير معروف"
            raise RuntimeError(f"فشل سحب التحديثات: {error_msg}")

        return _stdout.decode("utf-8", errors="replace")

    async def get_repository_status(self, owner: str, repo: str) -> dict[str, Any]:
        """Get git status of a local repository."""

        workspace_path = self.get_workspace_path(owner, repo)
        if not workspace_path.exists():
            raise FileNotFoundError(f"مساحة العمل غير موجودة: {workspace_path}")

        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace_path), "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, _stderr = await proc.communicate()

        output = _stdout.decode("utf-8", errors="replace")
        lines = [line for line in output.strip().split("\n") if line]

        return {
            "path": str(workspace_path),
            "has_changes": len(lines) > 0,
            "changed_files": lines,
            "change_count": len(lines),
        }
