"""Shared parsing helpers for external payloads (kept next to the billing engine)."""

from datetime import datetime, timezone
from typing import Any


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp coming from an external provider."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
