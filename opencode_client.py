"""Asynchronous client for the local OpenCode HTTP server."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

import httpx

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4096
TIMEOUT = 600.0


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
        password: Optional[str] = None,
        timeout: float = TIMEOUT,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        auth = ("opencode", password) if password else None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            timeout=httpx.Timeout(timeout, connect=15.0, write=60.0, pool=60.0),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("/global/health")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def health_check(self) -> bool:
        try:
            return bool((await self.health()).get("healthy"))
        except httpx.HTTPError:
            return False

    async def create_session(
        self,
        parent_id: Optional[str] = None,
        title: Optional[str] = None,
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

    async def send_prompt(
        self,
        session_id: str,
        text: str,
        model: Optional[dict[str, str] | str] = None,
        agent: Optional[str] = None,
        parts: list[dict[str, Any]] | None = None,
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
        response = await self._client.post(f"/session/{session_id}/message", json=body)
        response.raise_for_status()
        return response.json()

    async def abort_session(self, session_id: str) -> bool:
        response = await self._client.post(f"/session/{session_id}/abort")
        return response.status_code in (200, 204)

    async def share_session(self, session_id: str) -> Optional[str]:
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

    async def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        model: Optional[str] = None,
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
        return response.json()

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
        """Yield normalized, non-duplicated events for one OpenCode session.

        OpenCode wraps each Server-Sent Event inside a ``payload`` object and may
        emit a second ``sync`` representation of the same event. This method
        unwraps the primary payload and filters it by session ID, leaving callers
        with only the stable event type and properties structure.
        """
        import json

        request_timeout = self.timeout if timeout is None else timeout
        async with self._client.stream("GET", "/global/event", timeout=request_timeout) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
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
                    candidate_session_id = info.get("sessionID") or info.get("id")
                if session_id and candidate_session_id != session_id:
                    continue
                event_type = payload.get("type")
                if isinstance(event_type, str) and event_type:
                    yield {"type": event_type, "properties": properties}


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
        if isinstance(url, str) and isinstance(mime, str):
            item = {"url": url, "mime": mime}
            filename = part.get("filename")
            if isinstance(filename, str):
                item["filename"] = filename
            files.append(item)
    return files


async def wait_for_session_idle(
    client: OpenCodeClient,
    session_id: str,
    poll_interval: float = 1.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    elapsed = 0.0
    while elapsed < timeout:
        status = await client.get_session_status()
        state = status.get(session_id, {}).get("state", "")
        if state in {"idle", "complete", "done", ""}:
            break
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return {"state": status.get(session_id, {}).get("state", "unknown")}
