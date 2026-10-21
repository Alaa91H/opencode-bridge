"""Per-user IANA timezones; the host scheduler always stores UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

COUNTRIES = (
    ("🇩🇪 Germany / ألمانيا", "Europe/Berlin"),
    ("🇸🇾 Syria / سوريا", "Asia/Damascus"),
    ("🇹🇷 Türkiye / تركيا", "Europe/Istanbul"),
    ("🇸🇦 Saudi Arabia / السعودية", "Asia/Riyadh"),
    ("🇦🇪 UAE / الإمارات", "Asia/Dubai"),
    ("🇪🇬 Egypt / مصر", "Africa/Cairo"),
    ("🇱🇧 Lebanon / لبنان", "Asia/Beirut"),
    ("🇮🇶 Iraq / العراق", "Asia/Baghdad"),
    ("🇯🇴 Jordan / الأردن", "Asia/Amman"),
    ("🇵🇸 Palestine / فلسطين", "Asia/Gaza"),
    ("🇲🇦 Morocco / المغرب", "Africa/Casablanca"),
    ("🇫🇷 France / فرنسا", "Europe/Paris"),
    ("🇬🇧 United Kingdom / بريطانيا", "Europe/London"),
    ("🇺🇸 New York / أمريكا", "America/New_York"),
    ("🇺🇸 California / كاليفورنيا", "America/Los_Angeles"),
    ("🇨🇦 Toronto / كندا", "America/Toronto"),
    ("🇮🇳 India / الهند", "Asia/Kolkata"),
    ("🇯🇵 Japan / اليابان", "Asia/Tokyo"),
    ("🇦🇺 Sydney / أستراليا", "Australia/Sydney"),
    ("🌐 UTC", "UTC"),
)

def validate_zone(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError("Invalid IANA timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError("Unknown timezone") from exc
    return value

def local_to_utc(raw: str, zone_name: str) -> datetime:
    zone = ZoneInfo(validate_zone(zone_name))
    raw = raw.strip()
    if raw.endswith(" UTC"):
        raw = raw[:-4] + "+00:00"
    value = datetime.fromisoformat(raw)
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    aware = value.replace(tzinfo=zone, fold=0)
    instant = aware.astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != value:
        raise ValueError("Local time does not exist due to DST transition")
    return instant

def display_local(moment: datetime, zone_name: str) -> str:
    zone = ZoneInfo(validate_zone(zone_name))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
