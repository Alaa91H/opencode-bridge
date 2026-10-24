"""Detect optional media tools without installing or mutating the host."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
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


def detect_media_tools(disabled: Iterable[str] = ()) -> MediaToolCapabilities:
    """Detect installed tools, omitting capabilities the owner switched off."""
    blocked = set(disabled)
    return MediaToolCapabilities(
        ffmpeg=None if "ffmpeg" in blocked else shutil.which("ffmpeg"),
        ffprobe=None if "ffmpeg" in blocked else shutil.which("ffprobe"),
        tesseract=None if "tesseract" in blocked else shutil.which("tesseract"),
        pdftotext=None if "poppler" in blocked else shutil.which("pdftotext"),
        pdfimages=None if "poppler" in blocked else shutil.which("pdfimages"),
    )
