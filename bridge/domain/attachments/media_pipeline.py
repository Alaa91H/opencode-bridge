"""Media processing pipeline contracts: detect -> validate -> normalize -> extract -> segment -> agent -> compose."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class MediaInput:
    path: Path
    claimed_mime: str | None = None


@dataclass(frozen=True)
class MediaArtifact:
    kind: str
    path: Path | None = None
    text: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MediaAnalysis:
    kind: str
    detected_mime: str
    artifacts: tuple[MediaArtifact, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


class MediaProcessor(Protocol):
    def supports(self, media: MediaInput, detected_mime: str) -> bool: ...
    def process(self, media: MediaInput, detected_mime: str) -> MediaAnalysis: ...


def detect_mime(media: MediaInput) -> str:
    """Conservative built-in detection; magic inspection is strengthened in T36."""
    suffix_guess, _ = mimetypes.guess_type(media.path.name)
    return suffix_guess or media.claimed_mime or "application/octet-stream"


class MediaPipeline:
    def __init__(self, processors: list[MediaProcessor]) -> None:
        self.processors = tuple(processors)

    def analyze(self, media: MediaInput) -> MediaAnalysis:
        if not media.path.is_file():
            raise FileNotFoundError(media.path)
        detected = detect_mime(media)
        for processor in self.processors:
            if processor.supports(media, detected):
                return processor.process(media, detected)
        return MediaAnalysis(kind="binary", detected_mime=detected)
