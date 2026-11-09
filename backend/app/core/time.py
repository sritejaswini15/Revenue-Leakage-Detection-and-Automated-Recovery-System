"""Timezone helpers. All persisted timestamps are UTC."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC so comparisons work everywhere."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def hours_between(start: datetime, end: datetime) -> float:
    return max(0.0, (as_utc(end) - as_utc(start)).total_seconds() / 3600)
