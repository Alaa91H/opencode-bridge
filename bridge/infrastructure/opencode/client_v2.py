"""Typed OpenCode HTTP client with bounded retries, correlation IDs and reconnectable events."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx


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
    def __init__(self, config: OpenCodeHttpConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.metrics = OpenCodeMetrics()
        timeout = httpx.Timeout(config.read_timeout, connect=config.connect_timeout,
                                write=config.write_timeout, pool=config.pool_timeout)
        self.http = httpx.AsyncClient(base_url=config.base_url.rstrip("/"), timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, endpoint: str, *, json_body: dict[str, Any] | None = None,
                      correlation_id: str | None = None) -> OpenCodeResponse:
        cid = correlation_id or str(uuid.uuid4())
        headers = {"X-Correlation-ID": cid}
        last: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self.metrics.requests += 1
            try:
                response = await self.http.request(method, endpoint, json=json_body, headers=headers)
                if response.status_code in {429, 502, 503, 504} and attempt < self.config.max_retries:
                    self.metrics.retries += 1
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else self.config.retry_backoff * (2 ** attempt)
                    await __import__("asyncio").sleep(delay)
                    continue
                response.raise_for_status()
                payload = response.json() if response.content else {}
                return OpenCodeResponse(payload, cid, response.status_code)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last = exc
                if attempt >= self.config.max_retries:
                    break
                self.metrics.retries += 1
                await __import__("asyncio").sleep(self.config.retry_backoff * (2 ** attempt))
        self.metrics.failures += 1
        assert last is not None
        raise last

    async def create_message(self, request: OpenCodeRequest, *, correlation_id: str | None = None) -> OpenCodeResponse:
        body = {"prompt": request.prompt}
        if request.model is not None:
            body["model"] = request.model
        if request.session_id is not None:
            body["session_id"] = request.session_id
        return await self.request("POST", "/messages", json_body=body, correlation_id=correlation_id)

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
