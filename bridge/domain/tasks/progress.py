"""Structured task progress protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ProgressStage(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_MODEL = "waiting_model"
    PROCESSING_MEDIA = "processing_media"
    UPLOADING = "uploading"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ProgressEvent:
    task_id: str
    stage: ProgressStage
    detail: str = ""
    percent: float | None = None
    occurred_at: str = ""

    def __post_init__(self) -> None:
        if self.percent is not None and not 0 <= self.percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        if not self.occurred_at:
            object.__setattr__(self, "occurred_at", datetime.now(timezone.utc).isoformat())


class ProgressRenderer:
    LABELS = {
        ProgressStage.QUEUED: "Queued",
        ProgressStage.DOWNLOADING: "Downloading",
        ProgressStage.ANALYZING: "Analyzing",
        ProgressStage.PLANNING: "Planning",
        ProgressStage.EXECUTING: "Executing",
        ProgressStage.WAITING_MODEL: "Waiting for model",
        ProgressStage.PROCESSING_MEDIA: "Processing media",
        ProgressStage.UPLOADING: "Uploading",
        ProgressStage.COMPLETED: "Completed",
    }

    def render(self, event: ProgressEvent) -> str:
        text = self.LABELS[event.stage]
        if event.percent is not None:
            text += f" — {event.percent:g}%"
        if event.detail:
            text += f"\n{event.detail}"
        return text
