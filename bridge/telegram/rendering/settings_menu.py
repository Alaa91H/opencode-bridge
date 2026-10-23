"""Professional detailed command settings for all services."""
from __future__ import annotations

from typing import Any

# Professional grouped settings — used by cmd_config / cmd_settings
PROFESSIONAL_SETTINGS = {
    "services": {
        "reminder": {"poll_interval": 30, "history_limit": 500, "enabled": True},
        "scheduler": {"idle_wait": 30, "max_concurrent": 5, "poll_seconds": 5.0},
        "backup": {"retention_days": 3, "zip_compress": True, "auto_run": True},
        "queue": {"max_workers": 3, "poll_seconds": 5.0, "lease_seconds": 120},
        "tool_manager": {"cache_seconds": 60, "enabled": True, "log_changes": True},
        "github": {"token_configured": False, "auto_sync": False},
    },
    "telegram": {
        "message_max": 4000, "chunk_size": 3500, "long_text_enabled": True,
        "images_in_news": True, "deduplicate_commands": True,
    },
    "database": {
        "sqlite_cache_kib": 16000, "walmode": True, "optimize_on_shutdown": True,
    },
    "agent": {
        "correlation_header": "X-Correlation-Id", "circuit_breaker": True,
    },
}

class SettingsConfig:
    """Detailed customization endpoint for all bridge services."""

    def __init__(self) -> None:
        self._values = dict(PROFESSIONAL_SETTINGS)

    def get(self, section: str, key: str) -> Any:
        return self._values.get(section, {}).get(key)

    def set(self, section: str, key: str, value: Any) -> bool:
        if section not in self._values:
            self._values[section] = {}
        self._values[section][key] = value
        return True

    def render_status(self) -> str:
        lines = ["⚙️ **إعدادات الاحتراف — تفصيل كامل**"]
        for section, items in self._values.items():
            lines.append(f"\n🔧 **{section}:**")
            for k, v in items.items():
                lines.append(f"  • {k}: {v}")
        lines.append("\n---\n📌 يمكنك تعديل أي قيمة عبر /config [قسم] [مفتاح] [قيمة]")
        return "\n".join(lines)
