"""Detect optional media tools without installing or mutating the host."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class MediaToolCapabilities:
    ffmpeg: str | None
    ffprobe: str | None
    tesseract: str | None
    pdftotext: str | None
    pdfimages: str | None

    @property
    def video(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    @property
    def ocr(self) -> bool:
        return bool(self.tesseract)

    @property
    def pdf(self) -> bool:
        return bool(self.pdftotext)


def detect_media_tools() -> MediaToolCapabilities:
    return MediaToolCapabilities(
        ffmpeg=shutil.which("ffmpeg"),
        ffprobe=shutil.which("ffprobe"),
        tesseract=shutil.which("tesseract"),
        pdftotext=shutil.which("pdftotext"),
        pdfimages=shutil.which("pdfimages"),
    )
