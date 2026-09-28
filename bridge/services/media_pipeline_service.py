from __future__ import annotations

from bridge.domain.attachments.media_pipeline import MediaInput, MediaPipeline
from bridge.infrastructure.media.archive_processor import ZipArchiveProcessor
from bridge.infrastructure.media.processors import AudioProcessor, ImageProcessor, PdfProcessor, VideoProcessor
from bridge.infrastructure.media.tools import detect_media_tools


class MediaPipelineService:
    def __init__(self) -> None:
        tools = detect_media_tools()
        self.tools = tools
        self.pipeline = MediaPipeline([
            ImageProcessor(tools),
            PdfProcessor(tools),
            VideoProcessor(tools),
            AudioProcessor(tools),
            ZipArchiveProcessor(),
        ])

    def analyze(self, media: MediaInput):
        return self.pipeline.analyze(media)
