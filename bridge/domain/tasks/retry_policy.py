"""Classify provider failures without storing provider bodies or credentials."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
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


def classify_retry(exc: Exception) -> RetryDecision | None:
    """Quota waits never exhaust the retry budget; auth/config errors do."""
    status = 0
    body = ""
    delay = 60.0
    pending = False
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
        pending = exc.pending
    elif isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return RetryDecision("transport", 60.0, pending=True)
    else:
        return None

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
