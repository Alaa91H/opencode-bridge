"""GitHub API client layer for OpenCode Bridge.

Provides authenticated access to GitHub REST API v3 with:
- Automatic token authentication via GitHub App or Personal Access Token
- Rate limit handling and retry logic
- Pagination support
- Structured error responses
- Arabic error messages for Telegram users
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger("opencode_bridge.github.api")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_BASE = "https://api.github.com/graphql"

DEFAULT_PER_PAGE = 100
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub API rate limit is exceeded."""


class GitHubAuthenticationError(GitHubAPIError):
    """Raised when GitHub authentication fails."""


class GitHubNotFoundError(GitHubAPIError):
    """Raised when a GitHub resource is not found."""


class GitHubPermissionError(GitHubAPIError):
    """Raised when the authenticated user lacks permission."""


@dataclass
class GitHubConfig:
    """GitHub API configuration."""

    token: str = ""
    base_url: str = GITHUB_API_BASE
    timeout: float = 30.0
    max_retries: int = MAX_RETRIES
    retry_backoff: float = RETRY_BACKOFF
    user_agent: str = "opencode-bridge/3.59.0"


@dataclass
class GitHubResponse:
    """Standardized GitHub API response."""

    data: dict[str, Any] | list[dict[str, Any]] | None = None
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    rate_limit_remaining: int | None = None
    rate_limit_reset: datetime | None = None


class GitHubAPIClient:
    """Authenticated GitHub REST API client.

    Handles authentication, rate limiting, retries, and pagination
    for all GitHub API operations used by OpenCode Bridge.
    """

    def __init__(self, config: GitHubConfig | None = None) -> None:
        self.config = config or GitHubConfig()
        self._client: httpx.AsyncClient | None = None
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset: datetime | None = None

    async def __aenter__(self) -> GitHubAPIClient:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def start(self) -> None:
        """Initialize the HTTP client with authentication headers."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self.config.user_agent,
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            headers=headers,
            timeout=httpx.Timeout(self.config.timeout),
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.config.token)

    def _extract_rate_limit(self, headers: httpx.Headers) -> None:
        """Extract rate limit information from response headers."""
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        if remaining is not None:
            try:
                self._rate_limit_remaining = int(remaining)
            except ValueError:
                pass
        if reset is not None:
            try:
                self._rate_limit_reset = datetime.fromtimestamp(int(reset), tz=UTC)
            except (ValueError, OSError):
                pass

    def _handle_response(self, response: httpx.Response) -> dict[str, Any] | list[dict[str, Any]]:
        """Process HTTP response and extract data or raise appropriate error."""
        self._extract_rate_limit(response.headers)

        if response.status_code == 401:
            raise GitHubAuthenticationError(
                "فشل المصادقة مع GitHub. تأكد من صحة التوكن المكوّن.",
                status_code=401,
            )
        if response.status_code == 403:
            if self._rate_limit_remaining == 0:
                reset_str = ""
                if self._rate_limit_reset is not None:
                    reset_str = f" ينتهي عند {self._rate_limit_reset.strftime('%H:%M:%S UTC')}"
                raise GitHubRateLimitError(
                    f"تم تجاوز حد معدل طلبات GitHub API.{reset_str}",
                    status_code=403,
                )
            raise GitHubPermissionError(
                "لا يملك البوت صلاحية لتنفيذ هذه العملية على GitHub.",
                status_code=403,
            )
        if response.status_code == 404:
            raise GitHubNotFoundError(
                "المورد المطلوب غير موجود على GitHub أو لا يمكنك الوصول إليه.",
                status_code=404,
            )
        if response.status_code == 422:
            try:
                body = response.json()
                errors = body.get("errors", [])
                messages = [e.get("message", "") for e in errors if isinstance(e, dict)]
                detail = "; ".join(messages) if messages else "خطأ في التحقق من البيانات"
            except Exception:
                detail = "خطأ في التحقق من البيانات"
            raise GitHubAPIError(detail, status_code=422)
        if response.status_code >= 500:
            raise GitHubAPIError(
                f"خطأ في خادم GitHub (رمز الحالة {response.status_code}). يرجى المحاولة لاحقًا.",
                status_code=response.status_code,
            )

        try:
            return response.json()
        except Exception:
            return {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make an authenticated request to the GitHub API with retry logic."""
        if self._client is None:
            await self.start()
        assert self._client is not None  # noqa: S101 - start() always sets _client

        last_exception: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                return self._handle_response(response)
            except httpx.HTTPError as exc:
                last_exception = exc
                if attempt < self.config.max_retries:
                    wait = self.config.retry_backoff * (2**attempt)
                    log.warning("طلب GitHub فشل (محاولة %d/%d): %s. إعادة المحاولة بعد %d ثانية",
                               attempt + 1, self.config.max_retries + 1, exc, wait)
                    await asyncio.sleep(wait)
                else:
                    break
            except GitHubRateLimitError:
                # Don't retry rate limit errors immediately; wait until reset
                if self._rate_limit_reset is not None:
                    wait = max(1.0, (self._rate_limit_reset - datetime.now(UTC)).total_seconds())
                    log.warning("تم تجاوز حد معدل GitHub. انتظار %d ثانية حتى إعادة الضبط", wait)
                    await asyncio.sleep(min(wait, 300))
                    continue
                raise
            except GitHubAPIError:
                raise

        raise GitHubAPIError(
            f"فشل الاتصال بـ GitHub بعد {self.config.max_retries + 1} محاولات",
            response=None,
        ) from last_exception

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Send a GET request."""
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Send a POST request."""
        return await self._request("POST", path, json_body=json_body, params=params)

    async def patch(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Send a PATCH request."""
        return await self._request("PATCH", path, json_body=json_body, params=params)

    async def put(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Send a PUT request."""
        return await self._request("PUT", path, json_body=json_body, params=params)

    async def delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Send a DELETE request."""
        result = await self._request("DELETE", path, params=params)
        return result if result is not None else {}

    async def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Paginate through a GitHub API endpoint returning a list."""
        all_items: list[dict[str, Any]] = []
        page = 1
        current_params = dict(params or {})
        current_params["per_page"] = per_page

        while page <= max_pages:
            current_params["page"] = page
            result = await self.get(path, params=current_params)
            if not isinstance(result, list):
                break
            if not result:
                break
            all_items.extend(result)
            # Check if there's a next page via Link header
            # (httpx doesn't expose Link header easily; rely on per_page count)
            if len(result) < per_page:
                break
            page += 1

        return all_items

    async def get_rate_limit(self) -> dict[str, Any]:
        """Get current rate limit status."""
        return await self.get("/rate_limit")

    async def get_authenticated_user(self) -> dict[str, Any]:
        """Get the authenticated GitHub user profile."""
        return await self.get("/user")

    async def get_user_repos(
        self,
        *,
        affiliation: str = "owner,collaborator,organization_member",
        type: str = "all",
        sort: str = "updated",
        direction: str = "desc",
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[dict[str, Any]]:
        """List repositories accessible to the authenticated user."""
        return await self.paginate(
            "/user/repos",
            params={
                "affiliation": affiliation,
                "type": type,
                "sort": sort,
                "direction": direction,
                "per_page": per_page,
            },
        )
