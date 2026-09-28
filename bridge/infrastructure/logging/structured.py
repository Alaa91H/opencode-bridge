"""Privacy-safe structured JSON logging with bounded rotation."""

from __future__ import annotations

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from bridge.infrastructure.tracing.tracing import redact_attributes


class JsonFormatter(logging.Formatter):
    FIELDS = ("event", "task", "schedule", "trace", "attempt", "component", "duration", "status")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in self.FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        owner = getattr(record, "owner", None)
        if owner is not None:
            payload["owner_hash"] = hashlib.sha256(str(owner).encode()).hexdigest()[:16]
        attrs = getattr(record, "attributes", None)
        if isinstance(attrs, Mapping):
            payload["attributes"] = redact_attributes(attrs)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_logger(name: str, path: Path, *, max_bytes: int = 10 * 1024 * 1024,
                 backups: int = 7) -> logging.Logger:
    if max_bytes <= 0 or backups < 0:
        raise ValueError("invalid rotation policy")
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def safe_extra(**values: object) -> dict[str, object]:
    values.pop("prompt", None)
    values.pop("file_content", None)
    values.pop("attachment_content", None)
    return redact_attributes(values)
