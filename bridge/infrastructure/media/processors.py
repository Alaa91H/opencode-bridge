"""Capability-aware media processors. Optional tools are used only when already installed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bridge.domain.attachments.media_pipeline import MediaAnalysis, MediaArtifact, MediaInput
from .tools import MediaToolCapabilities


def _run(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=timeout)


class ImageProcessor:
    def __init__(self, tools: MediaToolCapabilities) -> None:
        self.tools = tools

    def supports(self, media: MediaInput, detected_mime: str) -> bool:
        return detected_mime.startswith("image/")

    def process(self, media: MediaInput, detected_mime: str) -> MediaAnalysis:
        artifacts: list[MediaArtifact] = []
        metadata = {"size_bytes": media.path.stat().st_size, "name": media.path.name}
        if self.tools.ffprobe:
            probe = _run([self.tools.ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", str(media.path)])
            data = json.loads(probe.stdout or "{}")
            stream = next(iter(data.get("streams", [])), {})
            metadata.update({k: stream[k] for k in ("width", "height", "pix_fmt") if k in stream})
        if self.tools.tesseract:
            ocr = _run([self.tools.tesseract, str(media.path), "stdout"])
            if ocr.stdout.strip():
                artifacts.append(MediaArtifact(kind="ocr", text=ocr.stdout))
        return MediaAnalysis("image", detected_mime, tuple(artifacts), metadata)


class PdfProcessor:
    def __init__(self, tools: MediaToolCapabilities, *, chunk_chars: int = 32_000) -> None:
        self.tools = tools
        self.chunk_chars = chunk_chars

    def supports(self, media: MediaInput, detected_mime: str) -> bool:
        return detected_mime == "application/pdf"

    def process(self, media: MediaInput, detected_mime: str) -> MediaAnalysis:
        artifacts: list[MediaArtifact] = []
        if self.tools.pdftotext:
            result = _run([self.tools.pdftotext, "-layout", str(media.path), "-"])
            text = result.stdout
            for start in range(0, len(text), self.chunk_chars):
                artifacts.append(MediaArtifact(kind="pdf_text_chunk", text=text[start:start + self.chunk_chars],
                                               metadata={"offset": start}))
        return MediaAnalysis("pdf", detected_mime, tuple(artifacts),
                             {"size_bytes": media.path.stat().st_size, "text_available": bool(self.tools.pdftotext),
                              "image_extraction_available": bool(self.tools.pdfimages)})


class VideoProcessor:
    def __init__(self, tools: MediaToolCapabilities) -> None:
        self.tools = tools

    def supports(self, media: MediaInput, detected_mime: str) -> bool:
        return detected_mime.startswith("video/")

    def process(self, media: MediaInput, detected_mime: str) -> MediaAnalysis:
        metadata: dict[str, object] = {"size_bytes": media.path.stat().st_size}
        if self.tools.ffprobe:
            result = _run([self.tools.ffprobe, "-v", "quiet", "-print_format", "json",
                           "-show_format", "-show_streams", str(media.path)])
            metadata["probe"] = json.loads(result.stdout or "{}")
        return MediaAnalysis("video", detected_mime, metadata=metadata)


class AudioProcessor:
    def __init__(self, tools: MediaToolCapabilities) -> None:
        self.tools = tools

    def supports(self, media: MediaInput, detected_mime: str) -> bool:
        return detected_mime.startswith("audio/")

    def process(self, media: MediaInput, detected_mime: str) -> MediaAnalysis:
        metadata: dict[str, object] = {"size_bytes": media.path.stat().st_size,
                                      "normalization_available": bool(self.tools.ffmpeg),
                                      "segmentation_available": bool(self.tools.ffmpeg),
                                      "stt_available": False,
                                      "speaker_segmentation_available": False}
        if self.tools.ffprobe:
            result = _run([self.tools.ffprobe, "-v", "quiet", "-print_format", "json",
                           "-show_format", "-show_streams", str(media.path)])
            metadata["probe"] = json.loads(result.stdout or "{}")
        return MediaAnalysis("audio", detected_mime, metadata=metadata)
