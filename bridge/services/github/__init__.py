"""GitHub project management services for OpenCode Bridge.

Provides comprehensive GitHub REST API integration including:
- Repository management (create, clone, configure, delete)
- Pull Request management (create, review, merge, close)
- Issue management (create, label, assign, comment)
- Release management (create, publish, upload assets)
- Branch management (create, delete, protect)
- Secrets and deploy keys management

All services use authenticated GitHub API v3 with rate limit
handling, retry logic, and Arabic error messages for Telegram users.
"""

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubConfig,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponse,
)
from bridge.services.github.branches import (
    GitHubBranch,
    GitHubBranchManager,
    GitHubBranchProtection,
)
from bridge.services.github.commands import GitHubCommandHandler
from bridge.services.github.issues import (
    GitHubIssue,
    GitHubIssueComment,
    GitHubIssueManager,
)
from bridge.services.github.pull_requests import (
    GitHubPRComment,
    GitHubPRReview,
    GitHubPullRequest,
    GitHubPullRequestManager,
)
from bridge.services.github.releases import (
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleaseManager,
)
from bridge.services.github.repositories import (
    GitHubRepoConfig,
    GitHubRepoInfo,
    GitHubRepositoryManager,
    LocalRepositoryManager,
)
from bridge.services.github.secrets import (
    GitHubDeployKey,
    GitHubSecret,
    GitHubSecretManager,
)

__all__ = [
    "GitHubAPIClient",
    "GitHubAPIError",
    "GitHubAuthenticationError",
    "GitHubBranch",
    "GitHubBranchManager",
    "GitHubBranchProtection",
    "GitHubCommandHandler",
    "GitHubConfig",
    "GitHubDeployKey",
    "GitHubIssue",
    "GitHubIssueComment",
    "GitHubIssueManager",
    "GitHubNotFoundError",
    "GitHubPRComment",
    "GitHubPRReview",
    "GitHubPermissionError",
    "GitHubPullRequest",
    "GitHubPullRequestManager",
    "GitHubRateLimitError",
    "GitHubRelease",
    "GitHubReleaseAsset",
    "GitHubReleaseManager",
    "GitHubRepoConfig",
    "GitHubRepoInfo",
    "GitHubRepositoryManager",
    "GitHubResponse",
    "GitHubSecret",
    "GitHubSecretManager",
    "LocalRepositoryManager",
]
