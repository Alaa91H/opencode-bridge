"""Telegram transport capabilities for cloud and Local Bot API modes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramCapabilities:
    mode: str
    local_paths: bool
    streaming_download: bool
    streaming_upload: bool
    api_base_url: str | None = None
    file_base_url: str | None = None


def capabilities(settings) -> TelegramCapabilities:
    local = settings.api_mode == "local"
    return TelegramCapabilities(
        mode=settings.api_mode,
        local_paths=local,
        streaming_download=True,
        streaming_upload=True,
        api_base_url=settings.local_api_base_url if local else None,
        file_base_url=settings.local_file_base_url if local else None,
    )
