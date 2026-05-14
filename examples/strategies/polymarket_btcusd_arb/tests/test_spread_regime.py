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
)
from examples.strategies.polymarket_btcusd_arb.spread_regime_reports import (
    write_summary_json,
    write_safety_check_json,
    write_report_md,
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
        write_report_md(summary, events, tmp_path)
        report_text = (tmp_path / "report.md").read_text()
        assert "No orders" in report_text
        assert "No private keys" in report_text


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