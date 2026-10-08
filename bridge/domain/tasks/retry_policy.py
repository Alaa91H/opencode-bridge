"""Classify provider failures without storing provider bodies or credentials."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


class ProviderTaskError(Exception):
    """OpenCode can return provider errors inside a successful HTTP response."""

    def __init__(self, error: dict[str, Any], *, pending: bool = False) -> None:
        super().__init__("provider task failed")
        self.error = error
        self.pending = pending


class TaskCancelledError(Exception):
    """The queue cancelled an execution before it could submit or deliver work."""


@dataclass(frozen=True)
class RetryDecision:
    category: str
    delay_seconds: float
    unlimited: bool = False
    pending: bool = False
    retry_at: datetime | None = None


def is_zen_free_model(model_id: str | None) -> bool:
    """Recognize OpenCode Zen models whose IDs explicitly carry the free suffix."""
    if not model_id:
        return False
    provider, separator, model = model_id.partition("/")
    return bool(
        separator
        and provider.casefold() in {"opencode", "opencode-zen"}
        and model.casefold().endswith("-free")
    )


def next_utc_midnight(now: datetime | None = None) -> datetime:
    """Return the next UTC day boundary used by the Zen free usage limiter."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    return (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _retry_after_matches_reset(value: str | None, reset_at: datetime, now: datetime | None) -> bool:
    if not value:
        return False
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    expected_seconds = (reset_at - reference.astimezone(UTC)).total_seconds()
    try:
        return abs(float(value) - expected_seconds) <= 2.0
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return abs((parsed.astimezone(UTC) - reset_at).total_seconds()) <= 2.0
        except (TypeError, ValueError, OverflowError):
            return False


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 60.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            reset = parsedate_to_datetime(value)
            if reset.tzinfo is None:
                reset = reset.replace(tzinfo=UTC)
            seconds = (reset - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 60.0
    return max(1.0, seconds) if math.isfinite(seconds) else 60.0


def classify_retry(
    exc: Exception,
    *,
    model_id: str | None = None,
    now: datetime | None = None,
) -> RetryDecision | None:
    """Quota waits never exhaust the retry budget; auth/config errors do."""
    status = 0
    body = ""
    delay = 60.0
    pending = False
    retry_after_value: str | None = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text.casefold()
        retry_after_value = exc.response.headers.get("Retry-After")
        delay = retry_after_seconds(retry_after_value)
    elif isinstance(exc, ProviderTaskError):
        data = exc.error.get("data", exc.error)
        if not isinstance(data, dict):
            data = {}
        try:
            status = int(data.get("statusCode") or 0)
        except (TypeError, ValueError):
            status = 0
        body = json.dumps(exc.error, ensure_ascii=False).casefold()
        headers = data.get("responseHeaders") or {}
        if isinstance(headers, dict):
            value = next((v for k, v in headers.items() if k.casefold() == "retry-after"), None)
            retry_after_value = str(value) if value is not None else None
            delay = retry_after_seconds(str(value) if value is not None else None)
        pending = exc.pending
    elif isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return RetryDecision("transport", 60.0, pending=True)
    else:
        return None

    reset_at = next_utc_midnight(now)
    zen_free_quota = is_zen_free_model(model_id) and (
        any(marker in body for marker in (
            "freeusagelimiterror",
            "free-models-per-day",
        ))
        or (status == 429 and _retry_after_matches_reset(retry_after_value, reset_at, now))
    )
    if zen_free_quota:
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        delay = max(1.0, (reset_at - reference.astimezone(UTC)).total_seconds())
        return RetryDecision("zen_free_quota", delay, unlimited=True, pending=pending, retry_at=reset_at)

    quota = any(marker in body for marker in (
        "insufficient_quota", "quota_exceeded", "quota exceeded", "usage limit",
        "rate_limit", "rate limit", "credit balance", "insufficient credit",
        "out of credits", "balance is too low", "exhausted", "spending limit",
        "billing limit", "daily limit", "monthly limit", "usage_limit", "no credits",
        "insufficient balance", "quota limit", "exceeded your current quota",
    ))
    if status in {402, 429} or (quota and status not in {401, 404}):
        return RetryDecision("quota", delay, unlimited=True, pending=pending)
    if status in {408, 500, 502, 503, 504}:
        return RetryDecision("provider_unavailable", delay, pending=pending)
    return None


def classify_credential_failure(
    exc: Exception,
    *,
    model_id: str | None = None,
    now: datetime | None = None,
) -> tuple[str, float] | None:
    """Classify failures that may be applied to one credential only."""
    decision = classify_retry(exc, model_id=model_id, now=now)
    if decision is not None and decision.category == "zen_free_quota":
        return ("zen_free_quota", decision.delay_seconds)

    status = 0
    body = ""
    delay = 60.0
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text.casefold()
        delay = retry_after_seconds(exc.response.headers.get("Retry-After"))
    elif isinstance(exc, ProviderTaskError):
        data = exc.error.get("data", exc.error)
        if not isinstance(data, dict):
            data = {}
        try:
            status = int(data.get("statusCode") or 0)
        except (TypeError, ValueError):
            status = 0
        body = json.dumps(exc.error, ensure_ascii=False).casefold()
        headers = data.get("responseHeaders") or {}
        if isinstance(headers, dict):
            value = next((v for k, v in headers.items() if k.casefold() == "retry-after"), None)
            delay = retry_after_seconds(str(value) if value is not None else None)

    if status == 401 or any(marker in body for marker in ("invalid api key", "invalid_api_key", "unauthorized credential")):
        return ("invalid_credential", delay)
    if status == 429:
        return ("credential_rate_limit", delay)
    if status == 402 or any(marker in body for marker in (
        "insufficient_quota", "quota_exceeded", "quota exceeded", "credit balance",
        "out of credits", "spending limit", "billing limit", "insufficient balance",
    )):
        return ("credential_quota", delay)
    return None
