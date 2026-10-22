"""Asynchronous client for the local OpenCode HTTP server.

This is the **production OpenCode client** used by the running bridge
(``bot.py``, ``workspace_runtime.py``, ``watchdog_runner.py``,
``maintenance/daily_agent_maintenance.py``, and the task execution service).

It sits alongside ``bridge/infrastructure/opencode/client_v2.py``
(``OpenCodeClientV2``), which is the newer typed client with bounded retries,
correlation IDs and reconnectable events. ``OpenCodeClientV2`` is the preferred
implementation for new code; this module remains the stable production entry
point until the two are fully consolidated (tracked under T46 legacy/V3
consolidation).

Helper functions defined here (``message_model_reference``,
``extract_text_response``, ``extract_file_response``) are shared across the
bridge and imported by ``model_manager.py``, ``task_execution_service.py`` and
``attachments.py``.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from bridge.domain.tasks.retry_policy import (
    ProviderTaskError,
    TaskCancelledError,
    classify_credential_failure,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4096
TIMEOUT = 60.0
# The three answers an agent permission request accepts.
PERMISSION_DECISIONS = frozenset({"once", "always", "reject"})


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open and requests are rejected."""

# Circuit breaker thresholds
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_RECOVERY_TIMEOUT = 30.0  # seconds before attempting half-open


@dataclass(frozen=True)
class CircuitState:
    failures: int = 0
    last_failure_time: float | None = None
    state: str = "closed"  # closed, open, half_open


class CircuitBreaker:
    """Circuit breaker for OpenCode API calls.

    Prevents cascading failures when OpenCode server is down by tracking
    consecutive failures and tripping open after a threshold. After the
    recovery timeout, allows one trial request (half-open) to test recovery.
    """

    def __init__(self, failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
                 recovery_timeout: float = CIRCUIT_RECOVERY_TIMEOUT,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.clock = clock
        self.state = CircuitState()

    def record_success(self) -> None:
        """Record a successful call, closing the circuit if half-open."""
        self.state = CircuitState(failures=0, state="closed")

    def record_failure(self) -> None:
        """Record a failed call, opening the circuit if threshold exceeded."""
        now = self.clock()
        state = self.state
        if state.state == "open":
            # Already open; just track the failure time
            self.state = CircuitState(
                failures=state.failures + 1,
                last_failure_time=now,
                state="open",
            )
            return
        new_failures = state.failures + 1
        if new_failures >= self.failure_threshold:
            self.state = CircuitState(
                failures=new_failures,
                last_failure_time=now,
                state="open",
            )
        else:
            self.state = CircuitState(
                failures=new_failures,
                last_failure_time=state.last_failure_time,
                state="closed",
            )

    def can_proceed(self) -> bool:
        """Check if a request is allowed through the circuit breaker."""
        state = self.state
        if state.state == "closed":
            return True
        if state.state == "open":
            now = self.clock()
            if state.last_failure_time is not None and now - state.last_failure_time >= self.recovery_timeout:
                # Half-open: allow one trial request
                self.state = CircuitState(
                    failures=state.failures,
                    last_failure_time=state.last_failure_time,
                    state="half_open",
                )
                return True
            return False
        # Half-open: allow the trial request
        return True

    def record_outcome(self, success: bool) -> None:
        """Record the outcome of a request."""
        if success:
            self.record_success()
        else:
            self.record_failure()


def message_model_reference(model: dict[str, str] | str) -> dict[str, str]:
    """Normalize a persisted ``provider/model`` ID for the message endpoint.

    OpenCode accepts a string when patching a session, but the message endpoint
    requires an object with ``providerID`` and ``modelID``. Keeping the
    conversion here prevents callers from accidentally using the wrong shape.
    """
    if isinstance(model, dict):
        provider_id = model.get("providerID")
        model_id = model.get("modelID")
        if isinstance(provider_id, str) and provider_id and isinstance(model_id, str) and model_id:
            return {"providerID": provider_id, "modelID": model_id}
        raise ValueError("OpenCode message model object requires providerID and modelID")
    if not isinstance(model, str) or "/" not in model:
        raise ValueError("OpenCode message model must use provider/model format")
    provider_id, model_id = model.split("/", 1)
    if not provider_id or not model_id:
        raise ValueError("OpenCode message model must use provider/model format")
    return {"providerID": provider_id, "modelID": model_id}


class OpenCodeClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        password: str | None = None,
        timeout: float = TIMEOUT,
        credential_pool: Any | None = None,
        credential_state_path: Any | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.base_url = f"http://{host}:{port}/api"
        self.timeout = max(1.0, min(60.0, timeout))
        self.credential_pool = credential_pool
        self.credential_state_path = credential_state_path
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        auth = ("opencode", password) if password else None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            timeout=httpx.Timeout(self.timeout, connect=15.0, write=60.0, pool=60.0),
            follow_redirects=False,
            trust_env=False,  # Credentials for loopback must never transit a host proxy.
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def set_provider_key(self, provider: str, secret: str) -> None:
        """Apply provider API authentication through the local OpenCode server."""
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", provider):
            raise ValueError("Invalid provider identifier")
        response = await self._client.put(
            f"/auth/{quote(provider, safe='')}", json={"type": "api", "key": secret})
        response.raise_for_status()

    async def close(self) -> None:
        self._save_credential_state()
        await self._client.aclose()

    def _save_credential_state(self) -> None:
        if self.credential_pool is not None and self.credential_state_path is not None:
            self.credential_pool.save_state(self.credential_state_path)

    async def _credential_request(self, method: str, path: str, *, model_id: str | None = None, **kwargs: Any) -> httpx.Response:
        """Apply credential failover only to provider-facing message submission."""
        if self.credential_pool is None or len(self.credential_pool) == 0:
            return await self._client.request(method, path, **kwargs)
        excluded: set[str] = set()
        original_headers = dict(kwargs.pop("headers", {}) or {})
        last_response: httpx.Response | None = None
        for _ in range(len(self.credential_pool)):
            credential = self.credential_pool.select(exclude=excluded)
            if credential is None:
                break
            headers = dict(original_headers)
            headers["Authorization"] = f"Bearer {credential.secret}"
            response = await self._client.request(method, path, headers=headers, **kwargs)
            last_response = response
            if response.is_success:
                self.credential_pool.record_success(credential.name)
                self._save_credential_state()
                return response
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                classified = classify_credential_failure(exc, model_id=model_id)
                if classified is None:
                    return response
                category, delay = classified
                if not self.credential_pool.record_failure(credential.name, category, retry_after=delay):
                    return response
                excluded.add(credential.name)
                self._save_credential_state()
        if last_response is not None:
            return last_response
        return await self._client.request(method, path, **kwargs)

    async def health(self) -> dict[str, Any]:
        # Use the sessions list endpoint as a health check with basic auth.
        # /api/session returns 200 with JSON when the server is up and authenticated.
        if not self.circuit_breaker.can_proceed():
            raise CircuitBreakerOpenError("OpenCode circuit breaker is open")
        try:
            response = await self._client.get("/session")
        except httpx.HTTPError:
            self.circuit_breaker.record_outcome(False)
            raise
        if response.is_success:
            self.circuit_breaker.record_outcome(True)
            return {"healthy": True, "status": "ok"}
        self.circuit_breaker.record_outcome(False)
        response.raise_for_status()
        return {"healthy": True, "status": "ok"}

    async def health_check(self) -> bool:
        try:
            return bool((await self.health()).get("healthy"))
        except httpx.HTTPError:
            return False

    async def create_session(
        self,
        parent_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if parent_id:
            body["parentID"] = parent_id
        if title:
            body["title"] = title
        response = await self._client.post("/session", json=body)
        response.raise_for_status()
        return response.json()

    async def delete_session(self, session_id: str) -> bool:
        response = await self._client.delete(f"/session/{session_id}")
        return response.status_code in (200, 204)

    async def list_session_messages(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"/session/{session_id}/message",
            params={"limit": max(1, min(int(limit), 200))},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    async def send_prompt(
        self,
        session_id: str,
        text: str,
        model: dict[str, str] | str | None = None,
        agent: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        variant: str | None = None,
        message_id: str | None = None,
        should_continue: Callable[[], Awaitable[bool]] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        message_parts = list(parts or [])
        if text:
            message_parts.insert(0, {"type": "text", "text": text})
        if not message_parts:
            raise ValueError("OpenCode message requires text or file parts")
        body: dict[str, Any] = {"parts": message_parts}
        if model:
            body["model"] = message_model_reference(model)
        if agent:
            body["agent"] = agent
        if variant:
            body["variant"] = variant
        if message_id:
            body["messageID"] = message_id
            return await self._send_resumable(session_id, message_id, body, should_continue)
        model_id = model if isinstance(model, str) else None
        response = await self._credential_request(
            "POST", f"/session/{session_id}/message", json=body, model_id=model_id,
            correlation_id=correlation_id,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {}
        return payload

    async def _task_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Every durable-task HTTP operation has a one-minute wall-clock limit."""
        try:
            async with asyncio.timeout(60.0):
                return await self._client.request(method, path, timeout=60.0, **kwargs)
        except TimeoutError as exc:
            raise httpx.ReadTimeout("OpenCode task request timed out") from exc

    async def _send_resumable(
        self, session_id: str, message_id: str, body: dict[str, Any],
        should_continue: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """Reconnect to persisted messages after restart without replaying a prompt.

        Submit asynchronously, then inspect status at short intervals. Provider
        retry states release the worker and retain the message ID for reconnection.
        """
        existing = await self._task_request("GET", f"/session/{session_id}/message/{message_id}")
        if existing.status_code == 404:
            if should_continue is not None and not await should_continue():
                raise TaskCancelledError("task cancelled before submission")
            accepted = await self._task_request("POST", f"/session/{session_id}/prompt_async", json=body)
            accepted.raise_for_status()
        else:
            existing.raise_for_status()

        idle_without_reply = 0
        while True:
            if should_continue is not None and not await should_continue():
                await self.abort_session(session_id)
                raise TaskCancelledError("task cancelled during execution")
            status_response = await self._task_request("GET", "/session/status")
            status_response.raise_for_status()
            status = status_response.json().get(session_id, {})
            state = status.get("type", status.get("state", "idle"))
            messages_response = await self._task_request("GET", f"/session/{session_id}/message", params={"limit": 200})
            messages_response.raise_for_status()
            messages = messages_response.json()
            if isinstance(messages, dict):
                messages = messages.get("items", [])
            replies = [item for item in messages if isinstance(item, dict)
                       and item.get("info", {}).get("role") == "assistant"
                       and item.get("info", {}).get("parentID") == message_id]
            replies.sort(key=lambda item: item.get("info", {}).get("time", {}).get("created", 0))
            if replies:
                latest = replies[-1]
                info = latest.get("info", {})
                if info.get("error"):
                    raise ProviderTaskError(info["error"])
                if state == "idle" and info.get("time", {}).get("completed"):
                    return latest
            if state == "retry":
                message = str(status.get("message", "provider temporarily unavailable"))
                raise ProviderTaskError({"data": {"statusCode": 503, "message": message}}, pending=True)
            if state == "idle":
                idle_without_reply += 1
                if idle_without_reply >= 3:
                    raise ProviderTaskError({"data": {"statusCode": 503, "message": "idle session interrupted before completion"}})
            else:
                idle_without_reply = 0
            await asyncio.sleep(5.0)

    async def abort_session(self, session_id: str) -> bool:
        response = await self._task_request("POST", f"/session/{session_id}/abort")
        return response.status_code in (200, 204)

    async def share_session(self, session_id: str) -> str | None:
        response = await self._client.post(f"/session/{session_id}/share")
        if response.status_code not in (200, 204):
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        slug = data.get("slug") or data.get("shareId")
        return f"https://opncd.ai/s/{slug}" if slug else None

    async def unshare_session(self, session_id: str) -> bool:
        response = await self._client.delete(f"/session/{session_id}/share")
        return response.status_code in (200, 204)

    async def list_permission_requests(self) -> list[dict[str, Any]]:
        """Pending permission requests across every session, oldest state kept."""
        response = await self._client.get("/permission")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            payload = payload.get("items", [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    async def reply_permission(
        self, request_id: str, decision: str, message: str = "",
    ) -> bool:
        """Answer an agent permission request: ``once``, ``always`` or ``reject``.

        Without this the agent waits on a decision nobody can give from Telegram,
        and the task burns its budget until the server's own timeout.
        """
        if decision not in PERMISSION_DECISIONS:
            raise ValueError("Unsupported permission decision")
        if not re.fullmatch(r"per[A-Za-z0-9_-]{0,63}", request_id):
            raise ValueError("Invalid permission request id")
        body: dict[str, Any] = {"reply": decision}
        if message:
            body["message"] = message[:400]
        response = await self._task_request(
            "POST", f"/permission/{quote(request_id, safe='')}/reply", json=body,
        )
        return response.status_code in (200, 204)

    async def list_questions(self) -> list[dict[str, Any]]:
        """Pending question requests: the agent is waiting on the operator."""
        response = await self._client.get("/question")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            payload = payload.get("items", [])
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    async def reply_question(self, request_id: str, answers: list[list[str]]) -> bool:
        """Answer the agent's question, one label list per question asked."""
        if not re.fullmatch(r"que[A-Za-z0-9_-]{0,63}", request_id):
            raise ValueError("Invalid question request id")
        if not answers or not all(isinstance(item, list) for item in answers):
            raise ValueError("Question answers must be one label list per question")
        response = await self._task_request(
            "POST",
            f"/question/{quote(request_id, safe='')}/reply",
            json={"answers": [[str(label)[:200] for label in item] for item in answers]},
        )
        return response.status_code in (200, 204)

    async def reject_question(self, request_id: str) -> bool:
        """Tell the agent its question was not answered, so it can carry on."""
        if not re.fullmatch(r"que[A-Za-z0-9_-]{0,63}", request_id):
            raise ValueError("Invalid question request id")
        response = await self._task_request(
            "POST", f"/question/{quote(request_id, safe='')}/reject",
        )
        return response.status_code in (200, 204)

    async def update_session(
        self,
        session_id: str,
        title: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if model is not None:
            body["model"] = model
        if not body:
            return {}
        response = await self._client.patch(f"/session/{session_id}", json=body)
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception:
            # Empty or non-JSON body still means the update succeeded.
            return {"id": session_id, "status": "ok"}
        return payload if isinstance(payload, dict) else {}

    async def list_providers(self) -> dict[str, Any] | list[dict[str, Any]]:
        response = await self._client.get("/provider")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, (dict, list)) else {}

    async def list_agents(self) -> list[dict[str, Any]]:
        response = await self._client.get("/agent")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def get_session_status(self) -> dict[str, Any]:
        response = await self._client.get("/session/status")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def stream_events(
        self,
        session_id: str,
        timeout: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Receive live OpenCode events with bounded SSE reconnections.

        The SSE stream is best effort; agents keep running independently if it
        fails. Retried streams may replay snapshots, deduplicated by the relay.
        """
        import json

        # SSE connections can be idle between long model/tool calls. A normal
        # 60-second response timeout is inappropriate for this channel.
        stream_timeout = httpx.Timeout(None, connect=15.0) if timeout is None else timeout
        last_id: str | None = None
        failures = 0
        while True:
            headers = {"Accept": "text/event-stream"}
            if last_id:
                headers["Last-Event-ID"] = last_id
            try:
                async with self._client.stream(
                    "GET", "/global/event", headers=headers, timeout=stream_timeout,
                ) as response:
                    response.raise_for_status()
                    failures = 0
                    async for line in response.aiter_lines():
                        if line.startswith("id:"):
                            last_id = line[3:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        try:
                            data = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        payload = data.get("payload", data)
                        if not isinstance(payload, dict) or payload.get("type") == "sync":
                            continue
                        properties = payload.get("properties", {})
                        if not isinstance(properties, dict):
                            continue
                        candidate_session_id = properties.get("sessionID")
                        part = properties.get("part")
                        info = properties.get("info")
                        if not candidate_session_id and isinstance(part, dict):
                            candidate_session_id = part.get("sessionID")
                        if not candidate_session_id and isinstance(info, dict):
                            candidate_session_id = info.get("sessionID")
                        if session_id and candidate_session_id != session_id:
                            continue
                        event_type = payload.get("type")
                        if isinstance(event_type, str) and event_type:
                            yield {"type": event_type, "properties": properties}
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                failures += 1
                if failures > 5:
                    raise exc
                await asyncio.sleep(min(8.0, 0.5 * (2 ** (failures - 1))))
            else:
                # Cleanly terminated streams may also reconnect, at a bounded
                # rate to avoid hot-spinning against a broken server.
                failures += 1
                if failures > 5:
                    return
                await asyncio.sleep(min(8.0, 0.5 * (2 ** (failures - 1))))


def extract_text_response(response: dict[str, Any]) -> str:
    texts: list[str] = []
    for part in response.get("parts", []):
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text", "")
            if text:
                texts.append(str(text))
    return "\n\n".join(texts)


def extract_file_response(response: dict[str, Any]) -> list[dict[str, str]]:
    """Return only well-formed file parts; path authorization happens in the bridge."""
    files: list[dict[str, str]] = []
    for part in response.get("parts", []):
        if not isinstance(part, dict) or part.get("type") != "file":
            continue
        url = part.get("url")
        mime = part.get("mime")
        filename = part.get("filename")
        if isinstance(url, str) and url:
            files.append({"url": url, "mime": str(mime or ""), "filename": str(filename or "")})
    return files
