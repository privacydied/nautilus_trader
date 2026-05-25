from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_asset_ctxs_fragment_validation import (
    validate_asset_ctxs_fragment,
)


def _write_manifest(path: Path, dates: list[date]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps({"date_list": [d.isoformat() for d in dates], "requested_symbols": ["BTC", "ETH"]}) + "\n",
        encoding="utf-8",
    )


def _write_rows(path: Path, symbol: str, rows: list[tuple[str, float, float]]) -> None:
    with (path / f"{symbol}.jsonl").open("w", encoding="utf-8") as fh:
        for ts, price, oi in rows:
            fh.write(json.dumps({"ts_event": ts, "symbol": symbol, "price": price, "open_interest": oi}) + "\n")


def test_constant_zero_oi_symbol_is_unusable_not_fragment_invalid(tmp_path: Path) -> None:
    dates = [date(2026, 1, 1)]
    _write_manifest(tmp_path, dates)
    _write_rows(tmp_path, "BTC", [("2026-01-01T00:00:00Z", 100.0, 10.0), ("2026-01-01T23:59:00Z", 101.0, 11.0)])
    _write_rows(tmp_path, "ETH", [("2026-01-01T00:00:00Z", 200.0, 0.0), ("2026-01-01T23:59:00Z", 200.0, 0.0)])

    result = validate_asset_ctxs_fragment(
        tmp_path,
        expected_first_date=date(2026, 1, 1),
        expected_last_date=date(2026, 1, 1),
        expected_date_count=1,
    )

    assert result.valid is True
    assert result.classification == "FRAGMENT_VALID_WITH_SYMBOL_COVERAGE_NOTES"
    assert result.unusable_symbols == ["ETH"]
    assert result.symbol_stats["ETH"].classification == "SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER"


def test_duplicate_timestamp_conflict_hard_fails(tmp_path: Path) -> None:
    dates = [date(2026, 1, 1)]
    _write_manifest(tmp_path, dates)
    _write_rows(tmp_path, "BTC", [("2026-01-01T00:00:00Z", 100.0, 10.0), ("2026-01-01T00:00:00Z", 101.0, 10.0)])

    result = validate_asset_ctxs_fragment(
        tmp_path,
        expected_first_date=date(2026, 1, 1),
        expected_last_date=date(2026, 1, 1),
        expected_date_count=1,
    )

    assert result.valid is False
    assert result.duplicate_timestamp_conflict_count == 1
    assert any(reason.startswith("duplicate_timestamp_conflicts") for reason in result.hard_fail_reasons)


def test_all_symbol_constant_zero_oi_fragment_hard_fails(tmp_path: Path) -> None:
    dates = [date(2026, 1, 1)]
    _write_manifest(tmp_path, dates)
    _write_rows(tmp_path, "BTC", [("2026-01-01T00:00:00Z", 100.0, 0.0), ("2026-01-01T23:59:00Z", 100.0, 0.0)])
    _write_rows(tmp_path, "ETH", [("2026-01-01T00:00:00Z", 200.0, 0.0), ("2026-01-01T23:59:00Z", 200.0, 0.0)])

    result = validate_asset_ctxs_fragment(
        tmp_path,
        expected_first_date=date(2026, 1, 1),
        expected_last_date=date(2026, 1, 1),
        expected_date_count=1,
    )

    assert result.valid is False
    assert "all_symbols_unusable_constant_or_zero_oi" in result.hard_fail_reasons


def test_partial_final_day_is_reported_not_silently_ignored(tmp_path: Path) -> None:
    dates = [date(2026, 1, 1), date(2026, 1, 2)]
    _write_manifest(tmp_path, dates)
    _write_rows(
        tmp_path,
        "BTC",
        [
            ("2026-01-01T00:00:00Z", 100.0, 10.0),
            ("2026-01-02T19:56:00Z", 101.0, 11.0),
        ],
    )

    result = validate_asset_ctxs_fragment(
        tmp_path,
        expected_first_date=date(2026, 1, 1),
        expected_last_date=date(2026, 1, 2),
        expected_date_count=2,
    )

    assert result.valid is True
    assert result.classification == "FRAGMENT_VALID_WITH_SOURCE_COVERAGE_CAVEATS"
    assert result.coverage_caveats == ["global_last_timestamp_not_2359:2026-01-02T19:56:00Z"]
