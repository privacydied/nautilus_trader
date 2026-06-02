"""Regression tests for bot manifest expiry datetime parsing (post-fix)."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC, timezone

import pytest

from examples.strategies.venue_agnostic_signal_observer.bot.manifest import (
    ManifestRecord,
    _parse_expiry_datetime,
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


class TestManifestExpiryDatetime:
    def test_lexicographic_wrong_answer_now_fixed(self):
        """After fix, datetime comparison is used, not string comparison."""
        now = datetime(2026, 1, 1, 0, 30, 0, tzinfo=timezone.utc)
        expires = datetime(2025, 12, 31, 23, 45, 0, tzinfo=timezone(timedelta(hours=-1)))
        record = _make_record(expires.isoformat())
        # After fix: is_expired uses datetime comparison, so not expired
        # But we can't inject 'now' into is_expired, so we test _parse_expiry_datetime
        parsed = _parse_expiry_datetime(record.expires_at_utc)
        assert parsed == expires
        assert now < parsed  # not expired

    def test_z_suffix_parsed_safely(self):
        """Z suffix must parse to UTC without error."""
        parsed = _parse_expiry_datetime("2026-06-01T12:00:00Z")
        assert parsed.tzinfo is not None
        assert parsed == datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_timezone_aware_before_now_is_expired(self):
        """Expiry in the past (UTC) must be expired."""
        expires = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        record = _make_record(expires)
        assert record.is_expired() is True

    def test_timezone_aware_after_now_is_not_expired(self):
        """Expiry in the future (UTC) must not be expired."""
        expires = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        record = _make_record(expires)
        assert record.is_expired() is False

    def test_same_instant_different_offsets_same_freshness(self):
        """2026-06-01T10:00:00+00:00 and 2026-06-01T11:00:00+01:00 are the same instant."""
        expires_utc = "2026-06-01T10:00:00+00:00"
        expires_plus1 = "2026-06-01T11:00:00+01:00"
        record_utc = _make_record(expires_utc)
        record_plus1 = _make_record(expires_plus1)
        assert record_utc.is_expired() == record_plus1.is_expired()

    def test_none_expiry_not_expired(self):
        """None expires_at means never expires."""
        record = _make_record(None)
        assert record.is_expired() is False

    def test_malformed_expiry_fail_closed(self):
        """Malformed expiry string must fail closed (expired=True)."""
        record = _make_record("not-a-datetime")
        assert record.is_expired() is True

    def test_naive_expiry_treated_as_utc(self):
        """Naive datetime string (no offset) should be treated as UTC."""
        expires = "2026-01-01T00:00:00"
        parsed = _parse_expiry_datetime(expires)
        assert parsed.tzinfo is timezone.utc
        # 2026-01-01 is in the past
        record = _make_record(expires)
        assert record.is_expired() is True

    def test_plus_offset_parsed_correctly(self):
        """Positive offset is parsed and compared correctly."""
        # 2026-06-01T11:00:00+01:00 = 2026-06-01T10:00:00Z
        parsed = _parse_expiry_datetime("2026-06-01T11:00:00+01:00")
        assert parsed == datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
