"""Read deployment flags through the same env-over-dotenv precedence as Settings."""
from __future__ import annotations

import os
from pathlib import Path

from bridge.config.settings import _parse_env_file

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def enabled(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        value = _parse_env_file(_PROJECT_ROOT / '.env').get(name)
    if value is None:
        return default
    val = value.strip().lower()
    if val not in {'1', '0', 'true', 'false', 'yes', 'no', 'on', 'off'}:
        raise ValueError(f'{name} must be a valid boolean setting')
    return val in {'1', 'true', 'yes', 'on'}
