"""GitHub Branches management for OpenCode Bridge.

Handles branch creation, deletion, protection rules, and status
tracking via the GitHub REST API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
)

log = logging.getLogger("opencode_bridge.github.branches")


@dataclass
class GitHubBranch:
    """Information about a GitHub Branch."""

    name: str
    commit_sha: str
    protected: bool
    url: str


@dataclass
class GitHubBranchProtection:
    """Information about branch protection rules."""

    enabled: bool
    required_status_checks: dict[str, Any] | None
    required_pull_request_reviews: dict[str, Any] | None
    restrictions: dict[str, Any] | None
    enforce_admins: bool | None
    required_linear_history: bool | None
    allow_force_pushes: bool | None
    allow_deletions: bool | None
    block_creations: bool | None
    required_conversation_resolution: bool | None


class GitHubBranchManager:
    """Manages GitHub branches and protection rules for OpenCode Bridge.

    Provides operations for creating, deleting, and protecting
    branches through the Telegram bridge.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client

    async def list_branches(
        self,
        owner: str,
        repo: str,
        *,
        protected: bool | None = None,
        per_page: int = 100,
    ) -> list[GitHubBranch]:
        """List branches in a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            protected: Filter by protection status.
            per_page: Items per page.

        Returns:
            List of GitHubBranch objects.
        """
        data = await self.client.get(f"/repos/{owner}/{repo}/branches", params={"per_page": per_page})
        branches = []
        for item in data if isinstance(data, list) else []:
            branch = GitHubBranch(
                name=item.get("name", ""),
                commit_sha=item.get("commit", {}).get("sha", ""),
                protected=item.get("protected", False),
                url=item.get("_links", {}).get("html", ""),
            )
            if protected is None or branch.protected == protected:
                branches.append(branch)
        return branches

    async def get_branch(self, owner: str, repo: str, branch: str) -> GitHubBranch:
        """Get information about a specific branch."""
        data = await self.client.get(f"/repos/{owner}/{repo}/branches/{branch}")
        return GitHubBranch(
            name=data.get("name", ""),
            commit_sha=data.get("commit", {}).get("sha", ""),
            protected=data.get("protected", False),
            url=data.get("_links", {}).get("html", ""),
        )

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        from_branch: str | None = None,
        from_sha: str | None = None,
    ) -> GitHubBranch:
        """Create a new branch in a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: New branch name.
            from_branch: Source branch name (defaults to default branch).
            from_sha: Source commit SHA (alternative to from_branch).

        Returns:
            GitHubBranch with details of the created branch.

        Raises:
            GitHubPermissionError: If lacking write permission.
            GitHubAPIError: If branch already exists.
        """
        # Get source SHA if not provided
        if from_sha is None:
            source = from_branch or await self._get_default_branch(owner, repo)
            source_info = await self.get_branch(owner, repo, source)
            from_sha = source_info.commit_sha

        body: dict[str, Any] = {
            "ref": f"refs/heads/{branch}",
            "sha": from_sha,
        }

        try:
            data = await self.client.post(f"/repos/{owner}/{repo}/git/refs", json_body=body)
        except GitHubAPIError as exc:
            log.error("فشل إنشاء فرع %s في %s/%s: %s", branch, owner, repo, exc)
            raise

        return GitHubBranch(
            name=data.get("ref", "").replace("refs/heads/", ""),
            commit_sha=data.get("object", {}).get("sha", ""),
            protected=False,
            url=data.get("url", ""),
        )

    async def delete_branch(self, owner: str, repo: str, branch: str) -> bool:
        """Delete a branch from a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Branch name to delete.

        Returns:
            True if deletion succeeded.
        """
        await self.client.delete(f"/repos/{owner}/{repo}/git/refs/heads/{branch}")
        return True

    async def get_branch_protection(
        self,
        owner: str,
        repo: str,
        branch: str,
    ) -> GitHubBranchProtection:
        """Get branch protection rules for a branch."""
        data = await self.client.get(f"/repos/{owner}/{repo}/branches/{branch}/protection")

        required_status_checks = data.get("required_status_checks")
        required_pr_reviews = data.get("required_pull_request_reviews")
        restrictions = data.get("restrictions")
        enforce_admins = data.get("enforce_admins")
        required_linear_history = data.get("required_linear_history")
        allow_force_pushes = data.get("allow_force_pushes")
        allow_deletions = data.get("allow_deletions")
        block_creations = data.get("block_creations")
        required_conversation_resolution = data.get("required_conversation_resolution")

        return GitHubBranchProtection(
            enabled=data.get("enabled", True),
            required_status_checks=required_status_checks if isinstance(required_status_checks, dict) else None,
            required_pull_request_reviews=required_pr_reviews if isinstance(required_pr_reviews, dict) else None,
            restrictions=restrictions if isinstance(restrictions, dict) else None,
            enforce_admins=enforce_admins,
            required_linear_history=required_linear_history,
            allow_force_pushes=allow_force_pushes,
            allow_deletions=allow_deletions,
            block_creations=block_creations,
            required_conversation_resolution=required_conversation_resolution,
        )

    async def update_branch_protection(
        self,
        owner: str,
        repo: str,
        branch: str,
        *,
        required_status_checks: dict[str, Any] | None = None,
        required_pull_request_reviews: dict[str, Any] | None = None,
        restrictions: dict[str, Any] | None = None,
        enforce_admins: bool | None = None,
        required_linear_history: bool | None = None,
        allow_force_pushes: bool | None = None,
        allow_deletions: bool | None = None,
        block_creations: bool | None = None,
        required_conversation_resolution: bool | None = None,
    ) -> GitHubBranchProtection:
        """Update branch protection rules for a branch."""
        body: dict[str, Any] = {}

        if required_status_checks is not None:
            body["required_status_checks"] = required_status_checks
        if required_pull_request_reviews is not None:
            body["required_pull_request_reviews"] = required_pull_request_reviews
        if restrictions is not None:
            body["restrictions"] = restrictions
        if enforce_admins is not None:
            body["enforce_admins"] = enforce_admins
        if required_linear_history is not None:
            body["required_linear_history"] = required_linear_history
        if allow_force_pushes is not None:
            body["allow_force_pushes"] = allow_force_pushes
        if allow_deletions is not None:
            body["allow_deletions"] = allow_deletions
        if block_creations is not None:
            body["block_creations"] = block_creations
        if required_conversation_resolution is not None:
            body["required_conversation_resolution"] = required_conversation_resolution

        data = await self.client.put(
            f"/repos/{owner}/{repo}/branches/{branch}/protection",
            json_body=body,
        )

        return GitHubBranchProtection(
            enabled=data.get("enabled", True),
            required_status_checks=data.get("required_status_checks"),
            required_pull_request_reviews=data.get("required_pull_request_reviews"),
            restrictions=data.get("restrictions"),
            enforce_admins=data.get("enforce_admins"),
            required_linear_history=data.get("required_linear_history"),
            allow_force_pushes=data.get("allow_force_pushes"),
            allow_deletions=data.get("allow_deletions"),
            block_creations=data.get("block_creations"),
            required_conversation_resolution=data.get("required_conversation_resolution"),
        )

    async def remove_branch_protection(self, owner: str, repo: str, branch: str) -> bool:
        """Remove branch protection rules for a branch."""
        await self.client.delete(f"/repos/{owner}/{repo}/branches/{branch}/protection")
        return True

    async def get_default_branch(self, owner: str, repo: str) -> str:
        """Get the default branch name of a repository."""
        from bridge.services.github.repositories import GitHubRepositoryManager
        repo_manager = GitHubRepositoryManager(self.client)
        return await repo_manager.get_default_branch(owner, repo)

    async def _get_default_branch(self, owner: str, repo: str) -> str:
        """Internal method to get default branch."""
        return await self.get_default_branch(owner, repo)
