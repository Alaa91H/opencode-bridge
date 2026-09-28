"""Lossless long-result representation independent of Telegram limits."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class OutputArtifact:
    name: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class OutputManifest:
    task_id: str
    summary: str
    full: OutputArtifact
    pages: tuple[str, ...] = ()


class LongOutputComposer:
    def __init__(self, *, summary_chars: int = 3000, page_chars: int = 3500) -> None:
        if summary_chars <= 0 or page_chars <= 0:
            raise ValueError("output limits must be positive")
        self.summary_chars = summary_chars
        self.page_chars = page_chars

    def compose(self, task_id: str, content: str, *, markdown: bool = True,
                paginate: bool = False) -> tuple[OutputManifest, bytes]:
        payload = content.encode("utf-8")
        suffix = "md" if markdown else "txt"
        media_type = "text/markdown" if markdown else "text/plain"
        summary = content if len(content) <= self.summary_chars else content[:self.summary_chars] + "\n…\nFull result attached."
        pages = tuple(
            content[i:i + self.page_chars] for i in range(0, len(content), self.page_chars)
        ) if paginate else ()
        artifact = OutputArtifact(
            name=f"{task_id}-result.{suffix}",
            media_type=media_type,
            size_bytes=len(payload),
            sha256=sha256(payload).hexdigest(),
        )
        return OutputManifest(task_id, summary, artifact, pages), payload
