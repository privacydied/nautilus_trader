"""Tests for shared UTC/time utility spine."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.core.time_utils import (
    is_expired_at,
    parse_iso_datetime,
    parse_iso_datetime_utc,
    to_utc_datetime,
)


_REPO_ROOT = Path(__file__).resolve().parents[6]


class TestParseIsoDatetime:
    def test_none_returns_none(self) -> None:
        assert parse_iso_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_iso_datetime("") is None

    def test_malformed_string_returns_none(self) -> None:
        assert parse_iso_datetime("not-a-timestamp") is None

    def test_parses_plus_zero_offset(self) -> None:
        result = parse_iso_datetime("2026-01-01T00:00:00+00:00")
        assert result == datetime(2026, 1, 1, tzinfo=UTC)

    def test_parses_trailing_z(self) -> None:
        result = parse_iso_datetime("2026-01-01T00:00:00Z")
        assert result == datetime(2026, 1, 1, tzinfo=UTC)

    def test_parses_non_utc_offset(self) -> None:
        result = parse_iso_datetime("2026-01-01T00:30:00+05:30")
        assert result is not None
        assert result.utcoffset() == timedelta(hours=5, minutes=30)


class TestToUtcDatetime:
    def test_aware_non_utc_converts_to_utc(self) -> None:
        # IST is UTC+5:30, so 00:30 IST = previous day 19:00 UTC
        source = datetime(2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        assert to_utc_datetime(source) == datetime(2025, 12, 31, 19, 0, tzinfo=UTC)

    def test_naive_treated_as_utc(self) -> None:
        source = datetime(2026, 1, 1, 0, 30)
        assert to_utc_datetime(source) == datetime(2026, 1, 1, 0, 30, tzinfo=UTC)


class TestParseIsoDatetimeUtc:
    def test_returns_aware_utc_datetime(self) -> None:
        result = parse_iso_datetime_utc("2026-01-01T00:00:00Z")
        assert result == datetime(2026, 1, 1, tzinfo=UTC)

    def test_same_instants_normalize_equal(self) -> None:
        a = parse_iso_datetime_utc("2026-01-01T00:00:00+00:00")
        b = parse_iso_datetime_utc("2025-12-31T19:00:00-05:00")
        assert a == b


class TestIsExpiredAt:
    def test_missing_value_returns_true_by_default(self) -> None:
        assert is_expired_at(None) is True

    def test_malformed_string_returns_true_by_default(self) -> None:
        assert is_expired_at("not-a-timestamp") is True

    def test_malformed_string_can_be_non_expired(self) -> None:
        assert is_expired_at("not-a-timestamp", malformed_is_expired=False) is False

    def test_past_aware_timestamp_is_expired(self) -> None:
        # Use a real past UTC expiry: 2025-12-31 23:45 UTC < 2026-01-01 00:00 UTC.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        expires_at = datetime(2025, 12, 31, 23, 45, tzinfo=UTC)
        assert is_expired_at(expires_at, now=now) is True

    def test_future_aware_timestamp_is_not_expired(self) -> None:
        # 2026-01-01 23:45 UTC > 2026-01-01 00:00 UTC.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        expires_at = datetime(2026, 1, 1, 23, 45, tzinfo=UTC)
        assert is_expired_at(expires_at, now=now) is False

    def test_handles_datetime_input_directly(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        past = datetime(2025, 12, 31, tzinfo=UTC)
        future = datetime(2026, 1, 2, tzinfo=UTC)
        assert is_expired_at(past, now=now) is True
        assert is_expired_at(future, now=now) is False


def _run_import_probe(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT.parent)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class TestCoreTimeUtilsImportContract:
    def test_module_import_is_dependency_light_subprocess(self) -> None:
        code = "import examples.strategies.venue_agnostic_signal_observer.core.time_utils as m; print('OK')"
        result = _run_import_probe(code)
        assert result.returncode == 0, f"import failed: {result.stderr}"
        assert "OK" in result.stdout
        for forbidden in (
            "examples.strategies.venue_agnostic_signal_observer.paper",
            "examples.strategies.venue_agnostic_signal_observer.bot",
            "examples.strategies.venue_agnostic_signal_observer.conductor",
            "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.node_fills_liq_reconstruction.runner",
            "examples.strategies.venue_agnostic_signal_observer.hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        ):
            assert forbidden not in result.stderr, f"stderr indicates forbidden import: {forbidden}\n{result.stderr}"

    def test_lexicographic_trap_handled(self) -> None:
        now = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
        expires_at = "2025-12-31T23:45:00-01:00"
        assert is_expired_at(expires_at, now=now) is False
