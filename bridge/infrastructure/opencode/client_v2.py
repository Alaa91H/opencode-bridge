"""Typed OpenCode HTTP client with bounded retries, correlation IDs and reconnectable events."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from bridge.domain.tasks.retry_policy import classify_credential_failure


@dataclass(frozen=True)
class OpenCodeHttpConfig:
    base_url: str
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    write_timeout: float = 120.0
    pool_timeout: float = 10.0
    max_retries: int = 3
    retry_backoff: float = 0.25


@dataclass(frozen=True)
class OpenCodeRequest:
    prompt: str
    model: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class OpenCodeResponse:
    data: dict[str, Any]
    correlation_id: str
    status_code: int


@dataclass
class OpenCodeMetrics:
    requests: int = 0
    retries: int = 0
    failures: int = 0
    reconnects: int = 0


class OpenCodeClientV2:
    def __init__(self, config: OpenCodeHttpConfig, *, transport: httpx.AsyncBaseTransport | None = None, credential_pool: Any | None = None) -> None:
        self.config = config
        self.metrics = OpenCodeMetrics()
        self.credential_pool = credential_pool
        timeout = httpx.Timeout(config.read_timeout, connect=config.connect_timeout,
                                write=config.write_timeout, pool=config.pool_timeout)
        self.http = httpx.AsyncClient(base_url=config.base_url.rstrip("/"), timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, endpoint: str, *, json_body: dict[str, Any] | None = None,\n                      correlation_id: str | None = None, model_id: str | None = None) -> OpenCodeResponse:\n        cid = correlation_id or str(uuid.uuid4())\n        last: Exception | None = None\n        excluded: set[str] = set()\n        for attempt in range(self.config.max_retries + 1):\n            headers = {"X-Correlation-ID": cid}\n            credential = self.credential_pool.select(exclude=excluded) if self.credential_pool is not None else None\n            if credential is not None:\n                headers["Authorization"] = f"Bearer {credential.secret}"\n            self.metrics.requests += 1\n            try:\n                response = await self.http.request(method, endpoint, json=json_body, headers=headers)\n                response.raise_for_status()\n                if credential is not None:\n                    self.credential_pool.record_success(credential.name)\n                payload = response.json() if response.content else {}\n                return OpenCodeResponse(payload, cid, response.status_code)\n            except httpx.HTTPStatusError as exc:\n                last = exc\n                classified = classify_credential_failure(exc, model_id=model_id)\n                if credential is not None and classified is not None:\n                    category, delay = classified\n                    rotate = self.credential_pool.record_failure(credential.name, category, retry_after=delay)\n                    if rotate:\n                        excluded.add(credential.name)\n                        if self.credential_pool.select(exclude=excluded) is not None and attempt < self.config.max_retries:\n                            self.metrics.retries += 1\n                            continue\n                if response.status_code in {429, 502, 503, 504} and attempt < self.config.max_retries:\n                    self.metrics.retries += 1\n                    retry_after = response.headers.get("Retry-After")\n                    delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else self.config.retry_backoff * (2 ** attempt)\n                    await __import__("asyncio").sleep(delay)\n                    continue\n                break\n            except (httpx.TimeoutException, httpx.NetworkError) as exc:\n                last = exc\n                if attempt >= self.config.max_retries:\n                    break\n                self.metrics.retries += 1\n                await __import__("asyncio").sleep(self.config.retry_backoff * (2 ** attempt))\n        self.metrics.failures += 1\n        if last is None:\n            raise RuntimeError("opencode request failed without a recorded cause")\n        raise last\n
    async def create_message(self, request: OpenCodeRequest, *, correlation_id: str | None = None) -> OpenCodeResponse:
        body = {"prompt": request.prompt}
        if request.model is not None:
            body["model"] = request.model
        if request.session_id is not None:
            body["session_id"] = request.session_id
        return await self.request("POST", "/messages", json_body=body, correlation_id=correlation_id, model_id=request.model)

    async def events(self, endpoint: str = "/events", *, reconnects: int = 3) -> AsyncIterator[dict[str, Any]]:
        last_event_id: str | None = None
        for attempt in range(reconnects + 1):
            headers = {"Accept": "text/event-stream"}
            if last_event_id:
                headers["Last-Event-ID"] = last_event_id
            try:
                async with self.http.stream("GET", endpoint, headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("id:"):
                            last_event_id = line[3:].strip()
                        elif line.startswith("data:"):
                            yield json.loads(line[5:].strip())
                return
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt >= reconnects:
                    raise
                self.metrics.reconnects += 1
                await __import__("asyncio").sleep(self.config.retry_backoff * (2 ** attempt))
