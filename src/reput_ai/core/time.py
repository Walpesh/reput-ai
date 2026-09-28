"""Helpers for working with timezone aware UTC timestamps."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current timezone aware UTC timestamp."""
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None, tz: timezone = timezone.utc) -> datetime | None:
    """Attach ``tz`` to naive timestamps.

    PostgreSQL keeps ``TIMESTAMP WITH TIME ZONE`` values aware, while SQLite (used by
    the test-suite) returns naive datetimes, so all comparisons go through this helper.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)
