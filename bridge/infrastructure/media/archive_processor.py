from __future__ import annotations

from bridge.domain.attachments.media_pipeline import MediaAnalysis, MediaArtifact, MediaInput
from .archives import ArchiveLimits, zip_manifest


class ZipArchiveProcessor:
    def __init__(self, *, limits: ArchiveLimits = ArchiveLimits()) -> None:
        self.limits = limits

    def supports(self, media: MediaInput, detected_mime: str) -> bool:
        return detected_mime in {"application/zip", "application/x-zip-compressed"} or media.path.suffix.lower() == ".zip"

    def process(self, media: MediaInput, detected_mime: str) -> MediaAnalysis:
        entries = zip_manifest(media.path, limits=self.limits)
        artifacts = tuple(
            MediaArtifact(kind="archive_entry", text=entry.name,
                          metadata={"compressed_size": entry.compressed_size,
                                    "uncompressed_size": entry.uncompressed_size})
            for entry in entries
        )
        return MediaAnalysis("archive", detected_mime, artifacts,
                             {"entry_count": len(entries), "listed_before_extract": True})
