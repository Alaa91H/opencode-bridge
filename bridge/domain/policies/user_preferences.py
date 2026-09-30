"""Typed per-owner user preferences."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class UserPreferences:
    timezone: str = "UTC"
    language: str = "en"
    notification_level: str = "normal"
    default_workspace: str | None = None
    default_execution_profile: str = "SAFE"
    model_preference: str | None = None
    model_variant: str | None = None
    model_pinned: bool = False
    output_style: str = "summary"
    retention_days: int = 30
    schedule_defaults: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Every other validation in this method raises ValueError; a raw
        # ZoneInfoNotFoundError would escape a caller that only catches
        # ValueError, so normalize it here.
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc
        if not self.language.strip():
            raise ValueError("language must not be empty")
        if self.notification_level not in {"silent", "errors", "normal", "verbose"}:
            raise ValueError("invalid notification_level")
        if self.default_execution_profile not in {"SAFE", "DEVELOPMENT", "POWER", "HOST_ADMIN"}:
            raise ValueError("invalid execution profile")
        if self.output_style not in {"summary", "full", "compact"}:
            raise ValueError("invalid output_style")
        for value in (self.model_preference, self.model_variant):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("model_preference and model_variant must be non-empty strings or None")
        if not isinstance(self.model_pinned, bool):
            raise ValueError("model_pinned must be a boolean")
        if self.model_pinned and not self.model_preference:
            raise ValueError("model_pinned requires model_preference")
        if self.model_variant and not self.model_preference:
            raise ValueError("model_variant requires model_preference")
        if self.retention_days < 0:
            raise ValueError("retention_days cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> UserPreferences:
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in value.items() if k in allowed})
