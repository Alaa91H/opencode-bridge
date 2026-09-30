from __future__ import annotations

from pathlib import Path

from bridge.domain.attachments.intelligence import (
    AttachmentContext,
    AttachmentIntelligenceRouter,
    AttachmentRoute,
)
from bridge.domain.attachments.media_pipeline import MediaInput
from bridge.services.media_pipeline_service import MediaPipelineService


class AttachmentIntelligenceService:
    def __init__(self) -> None:
        self.router = AttachmentIntelligenceRouter()
        self.media = MediaPipelineService()

    def route(self, path: Path, *, mime: str, query: str = "", max_direct_bytes: int = 8 * 1024 * 1024) -> AttachmentRoute:
        context = AttachmentContext(query=query, max_direct_bytes=max_direct_bytes)
        return self.router.route(path=path, mime=mime, size=path.stat().st_size, context=context)

    def analyze_for_context(self, path: Path, *, mime: str):
        route = self.route(path, mime=mime)
        if route.strategy == "direct":
            return route, None
        if route.strategy in {"pdf", "video", "audio", "archive"}:
            return route, self.media.analyze(MediaInput(path, mime))
        return route, None
