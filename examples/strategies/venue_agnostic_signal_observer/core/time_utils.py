"""Shared UTC/time utilities with explicit naive and malformed-input policies."""

from __future__ import annotations

from datetime import datetime

try:  # Python 3.11+
    from datetime import UTC
except ImportError:  # pragma: no cover - fallback for older runtimes
    from datetime import timezone as _timezone  # type: ignore[no-redef]

    UTC = _timezone.utc


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-like datetime string.

    Returns
    -------
    datetime | None
        A timezone-aware datetime when parsing succeeds, otherwise ``None``.

    Policies
    --------
    * ``None`` and empty strings return ``None``.
    * Trailing ``Z`` is normalized to ``+00:00`` before parsing.
    * Malformed input returns ``None`` and never raises.
    * Naive timestamps in input are returned as naive. Call ``to_utc_datetime``
      afterwards if normalization is required.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def to_utc_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Policies
    --------
    * Aware datetimes are converted to UTC.
    * Naive datetimes are treated as UTC by project convention.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_iso_datetime_utc(value: str | None) -> datetime | None:
    """Parse an ISO-like datetime and normalize to an aware UTC datetime."""
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    return to_utc_datetime(parsed)


def is_expired_at(
    expires_at: str | datetime | None,
    *,
    now: datetime | None = None,
    malformed_is_expired: bool = True,
) -> bool:
    """Return whether ``expires_at`` is expired relative to ``now``."""
    resolved_now = now if now is not None else datetime.now(UTC)
    if expires_at is None:
        return malformed_is_expired

    if isinstance(expires_at, str):
        candidate = parse_iso_datetime(expires_at)
        if candidate is None:
            return malformed_is_expired
        expires_at = candidate

    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_aware = expires_at.replace(tzinfo=UTC)
        else:
            expires_aware = expires_at.astimezone(UTC)
        resolved_now = to_utc_datetime(resolved_now)
        return expires_aware <= resolved_now

    return malformed_is_expired
