"""Regression tests for bot manifest expiry datetime parsing (post-fix)."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC, timezone

import pytest

from examples.strategies.venue_agnostic_signal_observer.bot.manifest import (
    ManifestRecord,
)


def _make_record(expires_at_utc: str | None) -> ManifestRecord:
    return ManifestRecord(
        candidate_hash="c1",
        grid_hash="g1",
        rule_description="test",
        conditions={},
        limits={},
        manifest_hash="ph",
        created_at_utc="2026-01-01T00:00:00+00:00",
        expires_at_utc=expires_at_utc,
    )


_FIXED_NOW = datetime(2026, 1, 1, 0, 30, 0, tzinfo=timezone.utc)


class TestManifestExpiryDatetime:
    def test_lexicographic_wrong_answer_now_fixed(self) -> None:
        """After fix, datetime comparison is used, not string comparison."""
        expires = datetime(2025, 12, 31, 23, 45, 0, tzinfo=timezone(timedelta(hours=-1)))
        record = _make_record(expires.isoformat())
        assert record.is_expired(now=_FIXED_NOW) is False

    def test_z_suffix_parsed_safely(self) -> None:
        """Z suffix must parse to UTC without error and compare correctly."""
        record = _make_record("2026-06-01T12:00:00Z")
        assert record.is_expired(now=_FIXED_NOW) is False

    def test_timezone_aware_before_now_is_expired(self) -> None:
        """Expiry in the past (UTC) must be expired."""
        expires = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        record = _make_record(expires)
        assert record.is_expired() is True

    def test_timezone_aware_after_now_is_not_expired(self) -> None:
        """Expiry in the future (UTC) must not be expired."""
        expires = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        record = _make_record(expires)
        assert record.is_expired() is False

    def test_same_instant_different_offsets_same_freshness(self) -> None:
        """2026-06-01T10:00:00+00:00 and 2026-06-01T11:00:00+01:00 are the same instant."""
        expires_utc = "2026-06-01T10:00:00+00:00"
        expires_plus1 = "2026-06-01T11:00:00+01:00"
        record_utc = _make_record(expires_utc)
        record_plus1 = _make_record(expires_plus1)
        assert record_utc.is_expired(now=_FIXED_NOW) == record_plus1.is_expired(
            now=_FIXED_NOW
        )

    def test_none_expiry_not_expired(self) -> None:
        """None expires_at means never expires."""
        record = _make_record(None)
        assert record.is_expired() is False

    def test_malformed_expiry_fail_closed(self) -> None:
        """Malformed expiry string must fail closed (expired=True)."""
        record = _make_record("not-a-datetime")
        assert record.is_expired() is True

    def test_naive_expiry_treated_as_utc(self) -> None:
        """Naive datetime string is treated as UTC and expired correctly."""
        record = _make_record("2026-01-01T00:00:00")
        assert record.is_expired(now=_FIXED_NOW) is True

    def test_plus_offset_parsed_correctly(self) -> None:
        """Positive offset is parsed and compared correctly."""
        expires_utc = "2026-06-01T10:00:00+00:00"
        expires_plus1 = "2026-06-01T11:00:00+01:00"
        record_utc = _make_record(expires_utc)
        record_plus1 = _make_record(expires_plus1)
        assert record_utc.is_expired(now=_FIXED_NOW) == record_plus1.is_expired(
            now=_FIXED_NOW
        )
