from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer import liquidation_flush_aftershock_reversal_venue_age_aware_phase0a as venue_age
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    LoadDiagnostics,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0A_PRECOMMITMENT.md"
ACTIVE_ARCHIVE = REPO_ROOT / "data/hyperliquid_oi_velocity_compression_phase0"


def row(symbol: str, ts: datetime, price: float, oi: float, order: int = 0) -> ArchiveRow:
    return ArchiveRow(ts, symbol, price, oi, "fixture", order)


def hourly_symbol(symbol: str, start: datetime, months: int = 12, event_offsets: list[int] | None = None) -> list[ArchiveRow]:
    rows = []
    event_offsets = event_offsets or []
    days = int(months * 31)
    price = 100.0
    oi = 1000.0
    events = set(event_offsets)
    file_order = 0
    for hour in range(days * 24 + 1):
        ts = start + timedelta(hours=hour)
        if hour in events:
            price = 90.0
            oi = 900.0
        elif hour - 8 in events:
            price = 100.0
            oi = 1000.0
        rows.append(row(symbol, ts, price, oi, file_order))
        file_order += 1
    return rows


def run_rows(rows: list[ArchiveRow]):
    return venue_age.run_phase0a_audit(
        rows,
        LoadDiagnostics(total_raw_rows=len(rows), loaded_rows=len(rows), source_paths=["fixture"]),
        PRECOMMITMENT,
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        repo_root=REPO_ROOT,
        archive_source_path="fixture",
        archive_backfill_invoked=False,
    )


def test_quarter_concentration_failure():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(8):
        offsets = [240 + s * 10 + i * 72 for i in range(14)]
        rows.extend(hourly_symbol(f"ALT{s}", start, months=12, event_offsets=offsets))
    result = run_rows(rows)
    assert result.summary["accepted_event_count_after_cooldown"] >= 100
    assert result.summary["status"] == venue_age.STATUS_QUARTER_CONCENTRATION
    assert result.summary["max_calendar_quarter_event_share"] > venue_age.MAX_QUARTER_EVENT_SHARE


def test_month_concentration_failure():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = []
    # Spread events across four quarters, but make January alone exceed the 20% month cap.
    offsets_by_symbol = [
        [240, 312, 384, 456, 528, 2400, 4200, 6000, 7800, 9000, 10200, 12000, 14000]
        for _ in range(8)
    ]
    for s, offsets in enumerate(offsets_by_symbol):
        rows.extend(hourly_symbol(f"ALT{s}", start, months=24, event_offsets=[o + s * 2 for o in offsets]))
    result = run_rows(rows)
    assert result.summary["accepted_event_count_after_cooldown"] >= 100
    assert result.summary["max_calendar_quarter_event_share"] <= venue_age.MAX_QUARTER_EVENT_SHARE
    assert result.summary["status"] == venue_age.STATUS_MONTH_CONCENTRATION
    assert result.summary["max_calendar_month_event_share"] > venue_age.MAX_MONTH_EVENT_SHARE


def test_single_symbol_archive_file_is_rejected():
    status, reason = venue_age.validate_archive_paths([ACTIVE_ARCHIVE / "BTC.jsonl"])
    assert status == venue_age.STATUS_INVALID_SINGLE_SYMBOL_FILE
    assert "single-symbol" in reason


def test_active_archive_directory_path_is_accepted():
    status, reason = venue_age.validate_archive_paths([ACTIVE_ARCHIVE])
    assert status is None
    assert reason == ""


def test_btc_eth_events_do_not_count_in_altcoin_gates():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = []
    rows.extend(hourly_symbol("BTC", start, months=36, event_offsets=[240 + i * 24 * 10 for i in range(100)]))
    rows.extend(hourly_symbol("ETH", start, months=36, event_offsets=[360 + i * 24 * 10 for i in range(100)]))
    for s in range(8):
        rows.extend(hourly_symbol(f"ALT{s}", start, months=36, event_offsets=[480 + s * 24 + i * 24 * 95 for i in range(10)]))
    result = run_rows(rows)
    assert result.summary["max_symbol_event_share_symbol"] not in {"BTC", "ETH"}
    assert result.summary["accepted_event_count_after_cooldown"] == len(result.accepted_events)
    assert all(event.symbol not in {"BTC", "ETH"} for event in result.accepted_events)
    assert all(a.symbol not in {"BTC", "ETH"} for a in result.symbol_coverage if a.status == "accepted")


def test_distribution_sums_match_accepted_events():
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = []
    for s in range(8):
        rows.extend(hourly_symbol(f"ALT{s}", start, months=36, event_offsets=[240 + s * 24 + i * 24 * 95 for i in range(10)]))
    result = run_rows(rows)
    accepted = result.summary["accepted_event_count_after_cooldown"]
    assert sum(result.summary["quarter_distribution"].values()) == accepted
    assert sum(result.summary["month_distribution"].values()) == accepted
    assert result.summary["distinct_quarters_with_events"] == len([v for v in result.summary["quarter_distribution"].values() if v])
    assert result.summary["distinct_months_with_events"] == len([v for v in result.summary["month_distribution"].values() if v])


def test_missing_distribution_fields_cannot_unlock_phase0b():
    summary = {
        "status": venue_age.ORIGINAL_READY,
        "unlocks_phase0b": True,
        "accepted_event_count_after_cooldown": 1,
    }
    status, reason = venue_age.validate_distribution_consistency(summary)
    assert status == venue_age.STATUS_INTERNAL_CONSISTENCY
    assert "distribution" in reason


def test_distribution_mismatch_cannot_unlock_phase0b():
    summary = {
        "status": venue_age.ORIGINAL_READY,
        "unlocks_phase0b": True,
        "accepted_event_count_after_cooldown": 2,
        "quarter_distribution": {"2022Q1": 1},
        "month_distribution": {"2022-01": 2},
        "distinct_quarters_with_events": 1,
        "distinct_months_with_events": 1,
    }
    status, reason = venue_age.validate_distribution_consistency(summary)
    assert status == venue_age.STATUS_INTERNAL_CONSISTENCY
    assert "quarter_distribution" in reason
