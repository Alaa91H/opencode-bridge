"""Backend-neutral agent contracts used by the bridge services."""

from bridge.domain.agent.backend import AgentBackend
from bridge.domain.agent.capabilities import BackendCapabilities
from bridge.domain.agent.errors import (
    AgentBackendError,
    BackendAuthError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from bridge.domain.agent.events import assistant_delta, assistant_message, idle_event, status_event

__all__ = [
    "AgentBackend",
    "AgentBackendError",
    "BackendAuthError",
    "BackendCapabilities",
    "BackendProtocolError",
    "BackendTimeoutError",
    "BackendUnavailableError",
    "assistant_delta",
    "assistant_message",
    "idle_event",
    "status_event",
]
