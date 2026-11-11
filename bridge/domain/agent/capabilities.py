"""Declared behaviour of one agent backend.

Capabilities are advertised instead of guessed so the UI can hide actions a
backend cannot honour (for example sharing a Freebuff TUI conversation).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendCapabilities:
    backend_id: str = "opencode"
    display_name: str = "OpenCode"
    streaming: bool = True
    attachments: bool = True
    model_selection: bool = True
    sharing: bool = True
    permissions: bool = True
    acp: bool = False

    def as_dict(self) -> dict[str, bool | str]:
        return {
            "backend_id": self.backend_id,
            "display_name": self.display_name,
            "streaming": self.streaming,
            "attachments": self.attachments,
            "model_selection": self.model_selection,
            "sharing": self.sharing,
            "permissions": self.permissions,
            "acp": self.acp,
        }
