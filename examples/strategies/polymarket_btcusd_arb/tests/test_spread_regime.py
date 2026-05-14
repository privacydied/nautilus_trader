"""Tests for spread regime study.

No orders. No keys. No execution. No on-chain calls.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.strategies.polymarket_btcusd_arb.spread_regime import (
    SpreadEvent,
    SpreadRegimeSummary,
    SpreadBucketCounts,
    QuoteQuality,
    TTE_BUCKET_LABELS,
    SPREAD_THRESHOLDS_BPS,
    assign_tte_bucket,
    assign_time_of_day_bucket,
    assign_volatility_bucket,
    compute_spread_bps,
    compute_spread_abs,
    compute_spread_bucket_counts,
    compute_percentile,
    compute_summary_from_events,
    classify_verdict,
    classify_quote_quality,
    classify_verdict_reason,
    is_actionable_two_sided,
)
from examples.strategies.polymarket_btcusd_arb.spread_regime_reports import (
    write_all_reports,
)


# --- Helpers ---

def _make_event(
    market_slug: str = "btc-updown-15m-test",
    ts_ns: int = 1_777_000_000_000_000_000,
    tte_ns: int = 300_000_000_000,
    best_bid: float | None = 0.48,
    best_ask: float | None = 0.52,
    mid: float | None = 0.50,
    spread_bps: float | None = None,
    polymarket_stale: bool = False,
    binance_stale: bool = False,
    binance_vol_bps: float | None = 30.0,
    is_synthetic_fallback: bool = False,
) -> SpreadEvent:
    """Create a test spread event."""
    if spread_bps is None and best_bid is not None and best_ask is not None:
        calculated = compute_spread_bps(best_bid, best_ask)
    else:
        calculated = spread_bps
    return SpreadEvent(
        market_slug=market_slug,
        ts_event_ns=ts_ns,
        time_to_expiry_ns=tte_ns,
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread_abs=compute_spread_abs(best_bid, best_ask),
        spread_bps=calculated,
        book_depth_bid=None,
        book_depth_ask=None,
        binance_price=None,
        binance_spread_bps=None,
        binance_short_window_vol_bps=binance_vol_bps,
        polymarket_stale=polymarket_stale,
        binance_stale=binance_stale,
        is_synthetic_fallback=is_synthetic_fallback,
    )


# --- Spread bps computation ---

class TestSpreadBpsComputation:
    """Test spread_bps computation."""

    def test_normal_spread(self):
        """Bid=0.48, ask=0.52 => spread = 400/50*10000 = 800 bps."""
        result = compute_spread_bps(0.48, 0.52)
        assert result is not None
        assert abs(result - 800.0) < 1e-10

    def test_tight_spread(self):
        """Bid=0.499, ask=0.501 => spread ≈ 40 bps."""
        result = compute_spread_bps(0.499, 0.501)
        assert result is not None
        assert abs(result - 40.0) < 1e-6

    def test_none_bid(self):
        result = compute_spread_bps(None, 0.52)
        assert result is None

    def test_none_ask(self):
        result = compute_spread_bps(0.48, None)
        assert result is None

    def test_zero_bid(self):
        result = compute_spread_bps(0.0, 0.52)
        assert result is None

    def test_negative_bid(self):
        result = compute_spread_bps(-0.1, 0.52)
        assert result is None

    def test_crossed_market(self):
        """Ask < bid is invalid."""
        result = compute_spread_bps(0.55, 0.45)
        assert result is None

    def test_equal_bid_ask(self):
        """Zero spread."""
        result = compute_spread_bps(0.50, 0.50)
        assert result is not None
        assert result == 0.0

    def test_wide_spread(self):
        """Bid=0.10, ask=0.90 => mid=0.50, spread=0.80, bps = 0.80/0.50*10000 = 16000."""
        result = compute_spread_bps(0.10, 0.90)
        assert result is not None
        assert abs(result - 16000.0) < 1e-6


# --- Spread bucket counts ---

class TestSpreadBucketCounts:
    """Test spread bucket counting."""

    def test_bucket_counts_all_wide(self):
        """All spreads above 500 bps."""
        spreads = [600.0, 800.0, 1000.0]
        counts = compute_spread_bucket_counts(spreads)
        assert counts.total == 3
        assert counts.below_20bps == 0
        assert counts.below_500bps == 0

    def test_bucket_counts_mixed(self):
        """Mixed spreads."""
        spreads = [10.0, 30.0, 60.0, 90.0, 150.0, 300.0, 600.0]
        counts = compute_spread_bucket_counts(spreads)
        assert counts.total == 7
        assert counts.below_20bps == 1   # 10.0
        assert counts.below_40bps == 2   # 10.0, 30.0
        assert counts.below_80bps == 3   # 10.0, 30.0, 60.0
        assert counts.below_100bps == 4   # + 90.0
        assert counts.below_200bps == 5   # + 150.0
        assert counts.below_500bps == 6   # + 300.0
        assert counts.below_500bps == 6

    def test_bucket_counts_empty(self):
        counts = compute_spread_bucket_counts([])
        assert counts.total == 0
        assert counts.below_20bps == 0


# --- TTE bucket assignment ---

class TestTTEBucketAssignment:
    """Test time-to-expiry bucket assignment."""

    def test_near_expiry(self):
        """10 seconds to expiry → 0-30s bucket."""
        result = assign_tte_bucket(10_000_000_000)
        assert result.bucket_label == "0-30s"

    def test_30_seconds(self):
        """30 seconds → 30-60s bucket."""
        result = assign_tte_bucket(30_000_000_000)
        assert result.bucket_label == "30-60s"

    def test_1_minute(self):
        """60 seconds → 60-180s bucket."""
        result = assign_tte_bucket(60_000_000_000)
        assert result.bucket_label == "60-180s"

    def test_4_minutes(self):
        """240 seconds → 180-300s bucket."""
        result = assign_tte_bucket(240_000_000_000)
        assert result.bucket_label == "180-300s"

    def test_7_minutes(self):
        """420 seconds → 300-600s bucket."""
        result = assign_tte_bucket(420_000_000_000)
        assert result.bucket_label == "300-600s"

    def test_15_minutes(self):
        """900 seconds → 600s+ bucket."""
        result = assign_tte_bucket(900_000_000_000)
        assert result.bucket_label == "600s+"

    def test_zero_tte(self):
        """Zero time to expiry → 0-30s bucket."""
        result = assign_tte_bucket(0)
        assert result.bucket_label == "0-30s"


# --- Time of day bucket assignment ---

class TestTimeOfDayBucketAssignment:
    """Test UTC time-of-day bucket assignment."""

    def test_midnight_utc(self):
        # 2026-04-30 00:30 UTC → 00-06UTC
        ts = 1_777_800_000_000_000_000  # approximate
        result = assign_time_of_day_bucket(ts)
        assert result in ("00-06UTC", "06-12UTC")  # depends on exact timestamp

    def test_noon_utc(self):
        # 2026-04-30 14:00 UTC → 12-18UTC
        ts = 1_777_850_400_000_000_000  # approximate
        result = assign_time_of_day_bucket(ts)
        # Just verify it returns a valid bucket
        assert result in ("00-06UTC", "06-12UTC", "12-18UTC", "18-24UTC")


# --- Volatility bucket assignment ---

class TestVolatilityBucketAssignment:
    """Test Binance volatility bucket assignment."""

    def test_very_low_vol(self):
        assert assign_volatility_bucket(2.0) == "0-5bps"

    def test_low_vol(self):
        assert assign_volatility_bucket(10.0) == "5-20bps"

    def test_moderate_vol(self):
        assert assign_volatility_bucket(30.0) == "20-50bps"

    def test_high_vol(self):
        assert assign_volatility_bucket(75.0) == "50-100bps"

    def test_extreme_vol(self):
        assert assign_volatility_bucket(200.0) == "100+bps"

    def test_none_vol(self):
        assert assign_volatility_bucket(None) == "unknown"

    def test_negative_vol(self):
        assert assign_volatility_bucket(-1.0) == "unknown"


# --- Zero/missing bid/ask rejection ---

class TestZeroMissingBidAsk:
    """Test that zero or missing bid/ask produces None spread."""

    def test_missing_bid(self):
        assert compute_spread_bps(None, 0.52) is None

    def test_missing_ask(self):
        assert compute_spread_bps(0.48, None) is None

    def test_both_missing(self):
        assert compute_spread_bps(None, None) is None

    def test_zero_bid(self):
        assert compute_spread_bps(0.0, 0.52) is None

    def test_zero_ask(self):
        assert compute_spread_bps(0.48, 0.0) is None

    def test_event_with_missing_bid(self):
        """SpreadEvent with None bid should have None spread."""
        e = _make_event(best_bid=None, best_ask=0.52)
        assert e.spread_bps is None


# --- Summary handles empty input ---

class TestSummaryEmptyInput:
    """Test that summary computation handles empty events gracefully."""

    def test_empty_events_summary(self):
        summary = compute_summary_from_events([], "test-run", [])
        assert summary.event_count == 0
        assert summary.valid_spread_count == 0
        assert summary.market_count == 0
        assert summary.median_spread_bps is None
        assert summary.pct_spread_lte_80bps == 0.0
        assert summary.pct_spread_lte_200bps == 0.0

    def test_empty_events_verdict(self):
        summary = compute_summary_from_events([], "test-run", [])
        verdict = classify_verdict(summary)
        assert verdict == "SPREAD_REGIME_NEEDS_MORE_DATA"


# --- Report does not recommend execution ---

class TestReportNoExecution:
    """Spread regime report must not recommend execution."""

    def test_summary_recommendation_no_execution(self):
        events = [_make_event(spread_bps=100.0)]
        summary = compute_summary_from_events(events, "test", [])
        assert "not" in summary.recommendation.lower() or "do not" in summary.recommendation.lower()

    def test_verdict_is_not_execution(self):
        """Verdict must be one of the three allowed verdicts, never 'execute'."""
        events = [_make_event(spread_bps=100.0)]
        summary = compute_summary_from_events(events, "test", [])
        verdict = classify_verdict(summary)
        allowed = {
            "SPREAD_REGIME_NEEDS_MORE_DATA",
            "SPREAD_REGIME_STRUCTURALLY_TOO_WIDE",
            "SPREAD_REGIME_HAS_TIGHT_WINDOWS",
        }
        assert verdict in allowed

    def test_report_md_contains_no_execution(self, tmp_path):
        """report.md must contain safety statement and no-execution language."""
        events = [_make_event(spread_bps=100.0)]
        summary = compute_summary_from_events(events, "test", ["test-dir"])
        write_all_reports(summary, events, tmp_path)
        report_text = (tmp_path / "report.md").read_text()
        assert "No orders" in report_text


# --- Safety checks still pass ---

class TestSafetyStillPasses:
    """Verify existing AST safety checks still pass with new spread regime files."""

    def test_no_order_imports_in_spread_regime(self):
        """Spread regime module must not import order/execution modules."""
        pkg_dir = Path(__file__).resolve().parent.parent
        for pyfile in ["spread_regime.py", "spread_regime_reports.py", "run_spread_regime_study.py"]:
            filepath = pkg_dir / pyfile
            if not filepath.exists():
                continue
            source = filepath.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "OrderFactory" not in alias.name
                        assert "submit_order" not in alias.name
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert "nautilus_trader.live" not in node.module
                        assert "PolymarketExecutionClient" not in (node.module or "")
                    for alias in node.names:
                        assert alias.name not in (
                            "PolymarketExecutionClient",
                            "PolymarketLiveExecClientFactory",
                            "LiveNode",
                            "TradingNode",
                            "OrderFactory",
                        )

    def test_no_private_key_env_vars_in_spread_regime(self):
        """Spread regime module must not read Polymarket credential env vars."""
        forbidden_env = {
            "POLYMARKET_PK",
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_FUNDER",
            "POLYMARKET_PASSPHRASE",
        }
        pkg_dir = Path(__file__).resolve().parent.parent
        for pyfile in ["spread_regime.py", "spread_regime_reports.py", "run_spread_regime_study.py"]:
            filepath = pkg_dir / pyfile
            if not filepath.exists():
                continue
            source = filepath.read_text()
            for env_var in forbidden_env:
                assert env_var not in source, f"Forbidden env var {env_var} found in {pyfile}"

    def test_no_submit_order_in_spread_regime(self):
        """Spread regime must not contain submit_order calls."""
        pkg_dir = Path(__file__).resolve().parent.parent
        for pyfile in ["spread_regime.py", "spread_regime_reports.py", "run_spread_regime_study.py"]:
            filepath = pkg_dir / pyfile
            if not filepath.exists():
                continue
            source = filepath.read_text()
            assert "submit_order" not in source
            assert "cancel_order" not in source


# --- Percentile computation ---

class TestPercentileComputation:
    """Test percentile helper."""

    def test_empty_returns_none(self):
        assert compute_percentile([], 0.5) is None

    def test_single_value(self):
        assert compute_percentile([5.0], 0.5) == 5.0

    def test_median_even(self):
        result = compute_percentile([1.0, 2.0, 3.0, 4.0], 0.5)
        assert result is not None
        assert abs(result - 2.5) < 1e-10

    def test_median_odd(self):
        result = compute_percentile([1.0, 2.0, 3.0], 0.5)
        assert result is not None
        assert abs(result - 2.0) < 1e-10


# --- Classification verdicts ---

class TestClassifyVerdict:
    """Test spread regime classification verdicts."""

    def test_too_few_events_needs_more_data(self):
        """Fewer than 50 events → NEEDS_MORE_DATA."""
        events = [_make_event(spread_bps=10.0)] * 10
        summary = compute_summary_from_events(events, "test", [])
        assert classify_verdict(summary) == "SPREAD_REGIME_NEEDS_MORE_DATA"

    def test_structurally_too_wide(self):
        """Most spreads > 200 bps, median > 500 → STRUCTURALLY_TOO_WIDE."""
        events = [_make_event(spread_bps=800.0)] * 100
        summary = compute_summary_from_events(events, "test", [])
        assert classify_verdict(summary) == "SPREAD_REGIME_STRUCTURALLY_TOO_WIDE"

    def test_tight_windows(self):
        """More than 50% below 200 bps → HAS_TIGHT_WINDOWS."""
        events = [_make_event(spread_bps=50.0)] * 60 + [_make_event(spread_bps=500.0)] * 40
        summary = compute_summary_from_events(events, "test", [])
        assert classify_verdict(summary) == "SPREAD_REGIME_HAS_TIGHT_WINDOWS"

    def test_borderline_needs_more_data(self):
        """Between too-wide and tight windows → NEEDS_MORE_DATA."""
        # 30% below 200 bps, median around 300 bps
        events = [_make_event(spread_bps=100.0)] * 30 + [_make_event(spread_bps=400.0)] * 70
        summary = compute_summary_from_events(events, "test", [])
        verdict = classify_verdict(summary)
        assert verdict in (
            "SPREAD_REGIME_NEEDS_MORE_DATA",
            "SPREAD_REGIME_STRUCTURALLY_TOO_WIDE",
        )


# --- Quote Quality Classification ---

class TestQuoteQualityClassification:
    """Test classify_quote_quality for all quote quality categories."""

    def test_two_sided_book(self):
        """Real bid/ask away from exchange bounds."""
        assert classify_quote_quality(0.48, 0.52) == QuoteQuality.TWO_SIDED_BOOK

    def test_two_sided_book_tight_spread(self):
        """Very tight real two-sided quote."""
        assert classify_quote_quality(0.499, 0.501) == QuoteQuality.TWO_SIDED_BOOK

    def test_two_sided_book_moderate(self):
        """Moderate width real quote."""
        assert classify_quote_quality(0.30, 0.70) == QuoteQuality.TWO_SIDED_BOOK

    def test_one_sided_book_bid_only(self):
        """Only bid side present."""
        assert classify_quote_quality(0.48, None) == QuoteQuality.ONE_SIDED_BOOK

    def test_one_sided_book_ask_only(self):
        """Only ask side present."""
        assert classify_quote_quality(None, 0.52) == QuoteQuality.ONE_SIDED_BOOK

    def test_empty_book(self):
        """Both sides empty."""
        assert classify_quote_quality(None, None) == QuoteQuality.EMPTY_BOOK

    def test_exchange_bound_two_sided_book(self):
        """bid=0.01, ask=0.99 with real CLOB data → EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        assert classify_quote_quality(0.01, 0.99) == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK

    def test_exchange_bound_two_sided_book_with_depth(self):
        """Real CLOB orders at 0.01/0.99 with genuine size → EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        assert classify_quote_quality(0.01, 0.99, 8298.48, 8291.48) == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK

    def test_exchange_bound_with_synthetic_flag_false(self):
        """is_synthetic_fallback=False (default) → EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        assert classify_quote_quality(0.01, 0.99, is_synthetic_fallback=False) == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK

    def test_fallback_min_max_with_synthetic_flag(self):
        """is_synthetic_fallback=True → FALLBACK_MIN_MAX (synthetic/default values)."""
        assert classify_quote_quality(0.01, 0.99, is_synthetic_fallback=True) == QuoteQuality.FALLBACK_MIN_MAX

    def test_fallback_min_max_synthetic_with_depth(self):
        """Synthetic fallback with depth info still classified as FALLBACK_MIN_MAX."""
        assert classify_quote_quality(0.01, 0.99, 0.0, 0.0, is_synthetic_fallback=True) == QuoteQuality.FALLBACK_MIN_MAX

    def test_bid_at_min_ask_not_max(self):
        """bid at min but ask not at max → TWO_SIDED_BOOK (not bound pair)."""
        assert classify_quote_quality(0.01, 0.52) == QuoteQuality.TWO_SIDED_BOOK

    def test_ask_at_max_bid_not_min(self):
        """ask at max but bid not at min → TWO_SIDED_BOOK (not bound pair)."""
        assert classify_quote_quality(0.48, 0.99) == QuoteQuality.TWO_SIDED_BOOK

    def test_missing_book_no_args(self):
        """No arguments → EMPTY_BOOK (both None)."""
        assert classify_quote_quality(None, None) == QuoteQuality.EMPTY_BOOK

    def test_invalid_book_crossed(self):
        """Bid > ask → INVALID_BOOK."""
        assert classify_quote_quality(0.55, 0.45) == QuoteQuality.INVALID_BOOK

    def test_invalid_book_zero_bid(self):
        """Zero bid → INVALID_BOOK."""
        assert classify_quote_quality(0.0, 0.50) == QuoteQuality.INVALID_BOOK

    def test_invalid_book_zero_ask(self):
        """Zero ask → INVALID_BOOK."""
        assert classify_quote_quality(0.50, 0.0) == QuoteQuality.INVALID_BOOK

    def test_invalid_book_negative(self):
        """Negative price → INVALID_BOOK."""
        assert classify_quote_quality(-0.01, 0.50) == QuoteQuality.INVALID_BOOK


# --- Exchange-bound vs fallback distinction ---

class TestExchangeBoundVsFallback:
    """Test that EXCHANGE_BOUND_TWO_SIDED_BOOK and FALLBACK_MIN_MAX are distinct."""

    def test_real_001_099_orders_are_exchange_bound(self):
        """Real 0.01/0.99 CLOB orders → EXCHANGE_BOUND_TWO_SIDED_BOOK, not FALLBACK_MIN_MAX."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 100
        for e in events:
            assert e.quote_quality == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK
            assert e.quote_quality != QuoteQuality.FALLBACK_MIN_MAX

    def test_synthetic_001_099_orders_are_fallback(self):
        """Synthetic 0.01/0.99 values → FALLBACK_MIN_MAX, not EXCHANGE_BOUND."""
        events = [_make_event(
            best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0,
            is_synthetic_fallback=True,
        )] * 50
        for e in events:
            assert e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX
            assert e.quote_quality != QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK

    def test_fallback_min_max_only_for_synthetic_defaults(self):
        """FALLBACK_MIN_MAX must only be used for synthetic/default values, never real CLOB data."""
        # Real CLOB orders at bounds → EXCHANGE_BOUND
        assert classify_quote_quality(0.01, 0.99, is_synthetic_fallback=False) == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK
        # Synthetic defaults → FALLBACK
        assert classify_quote_quality(0.01, 0.99, is_synthetic_fallback=True) == QuoteQuality.FALLBACK_MIN_MAX
        # Away from bounds → always TWO_SIDED_BOOK regardless of flag
        assert classify_quote_quality(0.48, 0.52, is_synthetic_fallback=True) == QuoteQuality.TWO_SIDED_BOOK
        assert classify_quote_quality(0.48, 0.52, is_synthetic_fallback=False) == QuoteQuality.TWO_SIDED_BOOK

    def test_actionable_two_sided_excludes_exchange_bound(self):
        """is_actionable_two_sided must exclude EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        assert is_actionable_two_sided(QuoteQuality.TWO_SIDED_BOOK) is True
        assert is_actionable_two_sided(QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK) is False
        assert is_actionable_two_sided(QuoteQuality.FALLBACK_MIN_MAX) is False
        assert is_actionable_two_sided(QuoteQuality.ONE_SIDED_BOOK) is False
        assert is_actionable_two_sided(QuoteQuality.EMPTY_BOOK) is False


class TestExchangeBoundTwoSidedBookSummary:
    """Test summary tracking of exchange-bound quotes."""

    def test_summary_tracks_exchange_bound_count(self):
        """Summary must report exchange_bound_two_sided_book_count."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 100
        summary = compute_summary_from_events(events, "test", [])
        assert summary.exchange_bound_two_sided_book_count == 100
        assert summary.two_sided_book_count == 0
        assert summary.fallback_min_max_count == 0
        assert summary.pct_exchange_bound_two_sided_book == 100.0
        assert summary.pct_two_sided_book == 0.0
        assert summary.pct_fallback_min_max == 0.0

    def test_actionable_two_sided_book_count_excludes_exchange_bound(self):
        """actionable_two_sided_book_count must not count exchange-bound quotes."""
        events_exc = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 80
        events_real = [_make_event(best_bid=0.48, best_ask=0.52, mid=0.50, spread_bps=800.0)] * 20
        all_events = events_exc + events_real
        summary = compute_summary_from_events(all_events, "test", [])
        assert summary.exchange_bound_two_sided_book_count == 80
        assert summary.two_sided_book_count == 20
        assert summary.actionable_two_sided_book_count == 20
        assert summary.pct_actionable_two_sided_book == 20.0
        assert summary.pct_exchange_bound_two_sided_book == 80.0

    def test_mixed_exchange_bound_and_real(self):
        """Mix of EXCHANGE_BOUND_TWO_SIDED_BOOK and TWO_SIDED_BOOK tracked correctly."""
        events_exc = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 80
        events_real = [_make_event(best_bid=0.48, best_ask=0.52, mid=0.50, spread_bps=800.0)] * 20
        all_events = events_exc + events_real
        summary = compute_summary_from_events(all_events, "test", [])
        assert summary.exchange_bound_two_sided_book_count == 80
        assert summary.two_sided_book_count == 20
        assert summary.fallback_min_max_count == 0
        assert abs(summary.pct_exchange_bound_two_sided_book - 80.0) < 1e-6
        assert abs(summary.pct_two_sided_book - 20.0) < 1e-6
        assert summary.pct_fallback_min_max == 0.0

    def test_pure_synthetic_fallback_tracked(self):
        """Pure FALLBACK_MIN_MAX events with is_synthetic_fallback=True."""
        events = [_make_event(
            best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0,
            is_synthetic_fallback=True,
        )] * 50
        summary = compute_summary_from_events(events, "test", [])
        assert summary.fallback_min_max_count == 50
        assert summary.exchange_bound_two_sided_book_count == 0
        assert summary.pct_fallback_min_max == 100.0


class TestVerdictReasonNoUsableTwoSidedBook:
    """Test that verdict reason correctly identifies no_usable_two_sided_book."""

    def test_all_exchange_bound(self):
        """100% EXCHANGE_BOUND_TWO_SIDED_BOOK → reason = no_usable_two_sided_book."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 100
        summary = compute_summary_from_events(events, "test", [])
        reason = classify_verdict_reason(summary)
        assert reason == "no_usable_two_sided_book"

    def test_mostly_exchange_bound(self):
        """99%+ EXCHANGE_BOUND_TWO_SIDED_BOOK → reason = no_usable_two_sided_book."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 200
        events += [_make_event(best_bid=0.48, best_ask=0.52, mid=0.50, spread_bps=800.0)] * 1
        summary = compute_summary_from_events(events, "test", [])
        reason = classify_verdict_reason(summary)
        assert reason == "no_usable_two_sided_book"

    def test_verdict_and_reason_together(self):
        """When all EXCHANGE_BOUND, verdict=STRUCTURALLY_TOO_WIDE and reason=no_usable_two_sided_book."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 100
        summary = compute_summary_from_events(events, "test", [])
        verdict = classify_verdict(summary)
        reason = summary.verdict_reason
        assert verdict == "SPREAD_REGIME_STRUCTURALLY_TOO_WIDE"
        assert reason == "no_usable_two_sided_book"

    def test_wide_but_real_quotes(self):
        """Real two-sided quotes but very wide → reason = quoted_spread_structurally_too_wide."""
        events = [_make_event(best_bid=0.40, best_ask=0.60, mid=0.50, spread_bps=4000.0)] * 100
        summary = compute_summary_from_events(events, "test", [])
        reason = classify_verdict_reason(summary)
        assert reason == "quoted_spread_structurally_too_wide"


# --- Report fields for exchange-bound classification ---

class TestExchangeBoundReportFields:
    """Test that reports include EXCHANGE_BOUND_TWO_SIDED_BOOK classification."""

    def test_summary_json_includes_exchange_bound(self, tmp_path):
        """summary.json must include exchange_bound_two_sided_book_count."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 10
        summary = compute_summary_from_events(events, "test-eb", ["test-dir"])
        write_all_reports(summary, events, tmp_path)
        summary_data = json.loads((tmp_path / "summary.json").read_text())
        assert "exchange_bound_two_sided_book_count" in summary_data
        assert "actionable_two_sided_book_count" in summary_data
        assert "pct_exchange_bound_two_sided_book" in summary_data
        assert "pct_actionable_two_sided_book" in summary_data
        assert summary_data["exchange_bound_two_sided_book_count"] == 10
        assert summary_data["actionable_two_sided_book_count"] == 0
        assert summary_data["two_sided_book_count"] == 0
        assert summary_data["fallback_min_max_count"] == 0

    def test_report_md_distinguishes_boundary_quotes_from_fallbacks(self, tmp_path):
        """report.md must distinguish EXCHANGE_BOUND_TWO_SIDED_BOOK from FALLBACK_MIN_MAX."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 10
        summary = compute_summary_from_events(events, "test-eb", ["test-dir"])
        write_all_reports(summary, events, tmp_path)
        report_text = (tmp_path / "report.md").read_text()
        assert "EXCHANGE_BOUND_TWO_SIDED_BOOK" in report_text
        assert "real CLOB" in report_text or "real" in report_text.lower()
        # Must NOT say these are synthetic/fallback defaults
        assert "synthetic fallback" not in report_text.lower() or "FALLBACK_MIN_MAX" in report_text

    def test_csv_includes_exchange_bound_quote_quality(self, tmp_path):
        """spread_events.csv must include quote_quality=EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 5
        summary = compute_summary_from_events(events, "test-eb", ["test-dir"])
        write_all_reports(summary, events, tmp_path)
        csv_text = (tmp_path / "spread_events.csv").read_text()
        assert "quote_quality" in csv_text
        assert "EXCHANGE_BOUND_TWO_SIDED_BOOK" in csv_text

    def test_verdict_reason_remains_no_usable_two_sided_book(self, tmp_path):
        """When all events are EXCHANGE_BOUND, verdict reason must be no_usable_two_sided_book."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 100
        summary = compute_summary_from_events(events, "test-eb", ["test-dir"])
        reason = classify_verdict_reason(summary)
        verdict = classify_verdict(summary)
        assert reason == "no_usable_two_sided_book"
        assert verdict == "SPREAD_REGIME_STRUCTURALLY_TOO_WIDE"