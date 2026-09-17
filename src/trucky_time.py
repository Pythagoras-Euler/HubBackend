"""Normalize Trucky ISO timestamps without discarding their UTC offset."""
from datetime import datetime, timezone


def normalize_time(value):
    if not value:
        return None
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def time_seconds(value):
    normalized = normalize_time(value)
    return int(datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()) if normalized else None
