"""GitHub Issues management for OpenCode Bridge.

Handles issue creation, assignment, labeling, and tracking
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

log = logging.getLogger("opencode_bridge.github.issues")


@dataclass
class GitHubIssue:
    """Information about a GitHub Issue."""

    id: int
    number: int
    title: str
    body: str | None
    state: str  # open, closed
    html_url: str
    author: str
    assignees: list[str]
    labels: list[str]
    created_at: str
    updated_at: str
    closed_at: str | None
    comments: int
    locked: bool
    milestone: str | None
    assignee: str | None


@dataclass
class GitHubIssueComment:
    """Information about an Issue comment."""

    id: int
    user: str
    body: str
    created_at: str
    html_url: str


class GitHubIssueManager:
    """Manages GitHub Issues for OpenCode Bridge.

    Provides operations for creating, updating, labeling, and
    tracking issues through the Telegram bridge.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str | None = None,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        milestone: int | None = None,
    ) -> GitHubIssue:
        """Create a new GitHub Issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: Issue title.
            body: Issue description.
            assignees: List of usernames to assign.
            labels: List of label names.
            milestone: Milestone number.

        Returns:
            GitHubIssue with details of the created issue.
        """
        body_dict: dict[str, Any] = {"title": title}
        if body is not None:
            body_dict["body"] = body
        if assignees:
            body_dict["assignees"] = assignees
        if labels:
            body_dict["labels"] = labels
        if milestone is not None:
            body_dict["milestone"] = milestone

        try:
            data = await self.client.post(f"/repos/{owner}/{repo}/issues", json_body=body_dict)
        except GitHubAPIError as exc:
            log.error("فشل إنشاء مسألة في %s/%s: %s", owner, repo, exc)
            raise

        return self._parse_issue(data)

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        """Get information about a specific Issue."""
        data = await self.client.get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        return self._parse_issue(data)

    async def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        labels: str | None = None,
        assignee: str | None = None,
        sort: str = "created",
        direction: str = "desc",
        per_page: int = 100,
    ) -> list[GitHubIssue]:
        """List Issues for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: Filter by state (open, closed, all).
            labels: Filter by comma-separated label names.
            assignee: Filter by assignee username.
            sort: Sort field (created, updated, comments).
            direction: Sort direction (asc, desc).
            per_page: Items per page.
        """
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": per_page,
        }
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee

        data = await self.client.get(f"/repos/{owner}/{repo}/issues", params=params)
        return [self._parse_issue(item) for item in data] if isinstance(data, list) else []

    async def close_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        """Close an Issue without merging."""
        data = await self.client.patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json_body={"state": "closed"},
        )
        return self._parse_issue(data)

    async def reopen_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        """Reopen a closed Issue."""
        data = await self.client.patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json_body={"state": "open"},
        )
        return self._parse_issue(data)

    async def edit_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> GitHubIssue:
        """Edit an Issue's title, body, or state."""
        body_dict: dict[str, Any] = {}
        if title is not None:
            body_dict["title"] = title
        if body is not None:
            body_dict["body"] = body
        if state is not None:
            body_dict["state"] = state

        data = await self.client.patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json_body=body_dict,
        )
        return self._parse_issue(data)

    async def add_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> list[str]:
        """Add labels to an Issue."""
        data = await self.client.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json_body=labels,
        )
        return [item.get("name", "") for item in data] if isinstance(data, list) else []

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
    ) -> bool:
        """Remove a label from an Issue."""
        await self.client.delete(
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
        )
        return True

    async def replace_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> list[str]:
        """Replace all labels on an Issue."""
        data = await self.client.put(
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json_body=labels,
        )
        return [item.get("name", "") for item in data] if isinstance(data, list) else []

    async def get_issue_comments(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> list[GitHubIssueComment]:
        """Get comments on an Issue."""
        data = await self.client.get(f"/repos/{owner}/{repo}/issues/{issue_number}/comments")
        return [self._parse_comment(item) for item in data] if isinstance(data, list) else []

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> GitHubIssueComment:
        """Create a comment on an Issue."""
        data = await self.client.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json_body={"body": body},
        )
        return self._parse_comment(data)

    async def assign_users(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        assignees: list[str],
    ) -> list[str]:
        """Assign users to an Issue."""
        data = await self.client.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
            json_body={"assignees": assignees},
        )
        return [a.get("login", "") for a in data.get("assignees", [])] if isinstance(data, dict) else []

    async def remove_assignees(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        assignees: list[str],
    ) -> bool:
        """Remove assignees from an Issue."""
        await self.client.delete(
            f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
            json_body={"assignees": assignees},
        )
        return True

    async def lock_issue(self, owner: str, repo: str, issue_number: int) -> bool:
        """Lock an Issue to prevent further comments."""
        await self.client.put(f"/repos/{owner}/{repo}/issues/{issue_number}/lock")
        return True

    async def unlock_issue(self, owner: str, repo: str, issue_number: int) -> bool:
        """Unlock an Issue to allow comments."""
        await self.client.delete(f"/repos/{owner}/{repo}/issues/{issue_number}/lock")
        return True

    async def list_labels(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List all labels for a repository."""
        return await self.client.get(f"/repos/{owner}/{repo}/labels")

    async def create_label(
        self,
        owner: str,
        repo: str,
        name: str,
        color: str = "ededed",
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new label for a repository."""
        body: dict[str, Any] = {"name": name, "color": color}
        if description:
            body["description"] = description
        return await self.client.post(f"/repos/{owner}/{repo}/labels", json_body=body)

    def _parse_issue(self, data: dict[str, Any]) -> GitHubIssue:
        """Convert raw API response to GitHubIssue dataclass."""
        user = data.get("user", {})
        assignee = data.get("assignee", {})
        labels = data.get("labels", [])
        assignees = data.get("assignees", [])
        milestone = data.get("milestone")

        return GitHubIssue(
            id=data.get("id", 0),
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=data.get("body"),
            state=data.get("state", "open"),
            html_url=data.get("html_url", ""),
            author=user.get("login", ""),
            assignees=[a.get("login", "") for a in assignees],
            labels=[item.get("name", "") for item in labels],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            closed_at=data.get("closed_at"),
            comments=data.get("comments", 0),
            locked=data.get("locked", False),
            milestone=milestone.get("title") if isinstance(milestone, dict) else None,
            assignee=assignee.get("login") if isinstance(assignee, dict) else None,
        )

    def _parse_comment(self, data: dict[str, Any]) -> GitHubIssueComment:
        """Convert raw API response to GitHubIssueComment dataclass."""
        user = data.get("user", {})
        return GitHubIssueComment(
            id=data.get("id", 0),
            user=user.get("login", ""),
            body=data.get("body", ""),
            created_at=data.get("created_at", ""),
            html_url=data.get("html_url", ""),
        )
