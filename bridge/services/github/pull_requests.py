"""GitHub Pull Request management for OpenCode Bridge.

Handles PR creation, review, merging, and status tracking
via the GitHub REST API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
)

log = logging.getLogger("opencode_bridge.github.pull_requests")


@dataclass
class GitHubPullRequest:
    """Information about a GitHub Pull Request."""

    id: int
    number: int
    title: str
    body: str | None
    state: str  # open, closed, merged
    html_url: str
    diff_url: str
    patch_url: str
    author: str
    head_ref: str
    base_ref: str
    head_sha: str
    base_sha: str
    created_at: str
    updated_at: str
    closed_at: str | None
    merged_at: str | None
    mergeable: bool | None
    mergeable_state: str | None
    comments: int
    review_comments: int
    commits: int
    additions: int
    deletions: int
    changed_files: int
    draft: bool
    labels: list[str] = None
    assignees: list[str] = None
    reviewers: list[str] = None


@dataclass
class GitHubPRReview:
    """Information about a PR review."""

    id: int
    user: str
    state: str  # approved, changes_requested, comment, pending
    body: str | None
    submitted_at: str
    html_url: str


@dataclass
class GitHubPRComment:
    """Information about a PR comment."""

    id: int
    user: str
    body: str
    created_at: str
    html_url: str
    path: str | None
    line: int | None


class GitHubPullRequestManager:
    """Manages GitHub Pull Requests for OpenCode Bridge.

    Provides operations for creating, reviewing, merging, and
    tracking PRs through the Telegram bridge.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str | None = None,
        draft: bool = False,
        maintainer_can_modify: bool = True,
    ) -> GitHubPullRequest:
        """Create a new Pull Request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: PR title.
            head: Source branch (e.g., "feature-branch" or "owner:feature-branch").
            base: Target branch (e.g., "main").
            body: PR description.
            draft: Whether to create as draft PR.
            maintainer_can_modify: Whether maintainers can modify the PR.

        Returns:
            GitHubPullRequest with details of the created PR.

        Raises:
            GitHubPermissionError: If lacking write permission.
            GitHubAPIError: For other API errors.
        """
        body_dict: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "draft": draft,
            "maintainer_can_modify": maintainer_can_modify,
        }
        if body is not None:
            body_dict["body"] = body

        try:
            data = await self.client.post(f"/repos/{owner}/{repo}/pulls", json_body=body_dict)
        except GitHubAPIError as exc:
            log.error("فشل إنشاء طلب سحب في %s/%s: %s", owner, repo, exc)
            raise

        return self._parse_pr(data)

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> GitHubPullRequest:
        """Get information about a specific Pull Request."""
        data = await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return self._parse_pr(data)

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        head: str | None = None,
        base: str | None = None,
        sort: str = "created",
        direction: str = "desc",
        per_page: int = 100,
    ) -> list[GitHubPullRequest]:
        """List Pull Requests for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: Filter by state (open, closed, all).
            head: Filter by head branch.
            base: Filter by base branch.
            sort: Sort field (created, updated, popularity, long-running).
            direction: Sort direction (asc, desc).
            per_page: Items per page.
        """
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": per_page,
        }
        if head:
            params["head"] = head
        if base:
            params["base"] = base

        data = await self.client.get(f"/repos/{owner}/{repo}/pulls", params=params)
        return [self._parse_pr(item) for item in data] if isinstance(data, list) else []

    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str = "merge",  # merge, squash, rebase
    ) -> dict[str, Any]:
        """Merge a Pull Request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: PR number.
            commit_title: Custom commit title.
            commit_message: Custom commit message.
            merge_method: Merge method (merge, squash, rebase).

        Returns:
            Merge result with SHA and status.
        """
        body: dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            body["commit_title"] = commit_title
        if commit_message:
            body["commit_message"] = commit_message

        return await self.client.put(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json_body=body,
        )

    async def close_pull_request(self, owner: str, repo: str, pr_number: int) -> GitHubPullRequest:
        """Close a Pull Request without merging."""
        data = await self.client.put(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            json_body={"state": "closed"},
        )
        return self._parse_pr(data)

    async def get_pull_request_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[GitHubPRReview]:
        """Get reviews for a Pull Request."""
        data = await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews")
        return [self._parse_review(item) for item in data] if isinstance(data, list) else []

    async def submit_pull_request_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        event: str,  # APPROVE, REQUEST_CHANGES, COMMENT
        body: str | None = None,
    ) -> GitHubPRReview:
        """Submit a review for a Pull Request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: PR number.
            event: Review event (APPROVE, REQUEST_CHANGES, COMMENT).
            body: Review body text.
        """
        body_dict: dict[str, Any] = {"event": event}
        if body is not None:
            body_dict["body"] = body

        data = await self.client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json_body=body_dict,
        )
        return self._parse_review(data)

    async def get_pull_request_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[GitHubPRComment]:
        """Get comments on a Pull Request."""
        data = await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}/comments")
        return [self._parse_comment(item) for item in data] if isinstance(data, list) else []

    async def create_pull_request_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        commit_id: str | None = None,
        path: str | None = None,
        line: int | None = None,
        side: str | None = None,  # LEFT, RIGHT
    ) -> GitHubPRComment:
        """Create a comment on a Pull Request."""
        body_dict: dict[str, Any] = {"body": body}
        if commit_id:
            body_dict["commit_id"] = commit_id
        if path:
            body_dict["path"] = path
        if line is not None:
            body_dict["line"] = line
        if side:
            body_dict["side"] = side

        data = await self.client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            json_body=body_dict,
        )
        return self._parse_comment(data)

    async def request_reviewers(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Request reviewers for a Pull Request."""
        body_dict: dict[str, Any] = {}
        if reviewers:
            body_dict["reviewers"] = reviewers
        if team_reviewers:
            body_dict["team_reviewers"] = team_reviewers

        return await self.client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            json_body=body_dict,
        )

    async def remove_requested_reviewers(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Remove requested reviewers from a Pull Request."""
        body_dict: dict[str, Any] = {}
        if reviewers:
            body_dict["reviewers"] = reviewers
        if team_reviewers:
            body_dict["team_reviewers"] = team_reviewers

        return await self.client.delete(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            json_body=body_dict,
        )

    async def get_pull_request_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        """Get files changed in a Pull Request."""
        return await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}/files")

    async def get_pull_request_commits(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        """Get commits in a Pull Request."""
        return await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}/commits")

    async def check_mergeability(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        """Check if a Pull Request can be merged."""
        data = await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return {
            "mergeable": data.get("mergeable"),
            "mergeable_state": data.get("mergeable_state"),
            "state": data.get("state"),
        }

    def _parse_pr(self, data: dict[str, Any]) -> GitHubPullRequest:
        """Convert raw API response to GitHubPullRequest dataclass."""
        user = data.get("user", {})
        head = data.get("head", {})
        base = data.get("base", {})

        return GitHubPullRequest(
            id=data.get("id", 0),
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=data.get("body"),
            state=data.get("state", "open"),
            html_url=data.get("html_url", ""),
            diff_url=data.get("diff_url", ""),
            patch_url=data.get("patch_url", ""),
            author=user.get("login", ""),
            head_ref=head.get("ref", ""),
            base_ref=base.get("ref", ""),
            head_sha=head.get("sha", ""),
            base_sha=base.get("sha", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            closed_at=data.get("closed_at"),
            merged_at=data.get("merged_at"),
            mergeable=data.get("mergeable"),
            mergeable_state=data.get("mergeable_state"),
            comments=data.get("comments", 0),
            review_comments=data.get("review_comments", 0),
            commits=data.get("commits", 0),
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
            changed_files=data.get("changed_files", 0),
            draft=data.get("draft", False),
            labels=[item.get("name", "") for item in data.get("labels", [])],
            assignees=[item.get("login", "") for item in data.get("assignees", [])],
            reviewers=[item.get("login", "") for item in data.get("requested_reviewers", [])],
        )

    def _parse_review(self, data: dict[str, Any]) -> GitHubPRReview:
        """Convert raw API response to GitHubPRReview dataclass."""
        user = data.get("user", {})
        return GitHubPRReview(
            id=data.get("id", 0),
            user=user.get("login", ""),
            state=data.get("state", "pending"),
            body=data.get("body"),
            submitted_at=data.get("submitted_at", ""),
            html_url=data.get("html_url", ""),
        )

    def _parse_comment(self, data: dict[str, Any]) -> GitHubPRComment:
        """Convert raw API response to GitHubPRComment dataclass."""
        user = data.get("user", {})
        return GitHubPRComment(
            id=data.get("id", 0),
            user=user.get("login", ""),
            body=data.get("body", ""),
            created_at=data.get("created_at", ""),
            html_url=data.get("html_url", ""),
            path=data.get("path"),
            line=data.get("line"),
        )
