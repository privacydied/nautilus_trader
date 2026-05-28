"""Tests for L2 summary coverage probe utilities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
    _quantile,
    _safe_float,
    _safe_int,
    compute_liquidity_stats,
    parse_api_symbol,
)


class TestQuantile:
    def test_quantile_empty(self):
        assert _quantile([], 0.5) == 0.0

    def test_quantile_single(self):
        assert _quantile([10.0], 0.5) == 10.0

    def test_quantile_median(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _quantile(vals, 0.5) == 3.0

    def test_quantile_p90(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        q = _quantile(vals, 0.9)
        # Implementation uses nearest-rank: p90 of [1..5] = 4.0
        assert q == 4.0


class TestLiquidityStats:
    def test_empty_rows(self):
        stats = compute_liquidity_stats([])
        assert stats["snapshot_count"] == 0

    def test_stats_computed(self):
        rows = [
            {"spread_bps": 10.0, "two_sided_book": True, "depth_cap_hit_top20": False, "empty_bid_side": False, "empty_ask_side": False},
            {"spread_bps": 20.0, "two_sided_book": True, "depth_cap_hit_top20": False, "empty_bid_side": False, "empty_ask_side": False},
            {"spread_bps": 30.0, "two_sided_book": False, "depth_cap_hit_top20": False, "empty_bid_side": True, "empty_ask_side": False},
        ]
        stats = compute_liquidity_stats(rows)
        assert stats["snapshot_count"] == 3
        assert abs(stats["two_sided_book_rate"] - 2.0 / 3.0) < 0.01
        assert abs(stats["median_spread_bps"] - 20.0) < 0.1


class TestParseApiSymbol:
    def test_dex_qualified(self):
        display, dex = parse_api_symbol("xyz:TSLA")
        assert display == "TSLA"
        assert dex == "xyz"

    def test_bare_symbol(self):
        display, dex = parse_api_symbol("TSLA")
        assert display == "TSLA"
        assert dex == "unknown"


class TestSafeHelpers:
    def test_safe_float_none(self):
        assert _safe_float(None) == 0.0

    def test_safe_int_none(self):
        assert _safe_int(None) == 0
