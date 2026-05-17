"""Focused tests for mcpt_export module.

Tests:
1. Skips MCPT when all mean_net_bps values are around -50 bps.
2. Selects a positive post-cost candidate.
3. Selects a near-breakeven group only if it beats baseline.
4. Rejects groups below min_events.
5. Does not crash on None/NaN/inf stats.
6. Exports a clean candidate CSV/JSONL with expected fields.
7. Selects at most max_groups.
8. Does not select duplicate variants of the same candidate.
"""
from __future__ import annotations

import csv
import json
import math
import tempfile
from pathlib import Path

import pytest

from venue_agnostic_signal_observer.mcpt_export import (
    DEFAULT_COST_FLOOR_BPS,
    DEFAULT_MAX_GROUPS,
    DEFAULT_MIN_EVENTS,
    _slugify,
    export_mcpt_candidate_series,
    export_mcpt_summary,
    is_mcpt_worthy_group,
    select_mcpt_candidate_groups,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_group(
    *,
    mean_net_bps: float = -54.0,
    median_net_bps: float = -55.0,
    win_rate: float = 0.0,
    baseline_mean_net_bps: float | None = None,
    baseline_win_rate: float | None = None,
    valid_count: int = 400,
    candidate: bool = False,
    rejection_reasons: list[str] | None = None,
    source_venue: str = "binance_perp",
    target_venue: str = "kraken",
    signal_type: str = "notional_burst",
    lookback_ms: int = 1000,
    horizon_ms: int = 5000,
) -> dict:
    """Build a mock results_by_group entry."""
    return {
        "source_venue": source_venue,
        "target_venue": target_venue,
        "signal_type": signal_type,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "valid_count": valid_count,
        "mean_raw_bps": mean_net_bps + 50.0,
        "mean_net_bps": mean_net_bps,
        "median_net_bps": median_net_bps,
        "win_rate": win_rate,
        "baseline_mean_net_bps": baseline_mean_net_bps,
        "baseline_win_rate": baseline_win_rate,
        "candidate": candidate,
        "rejection_reasons": rejection_reasons or [],
    }


# ---------------------------------------------------------------------------
# Test 1: Skip MCPT when all groups are cost-floor dust (~-50 bps)
# ---------------------------------------------------------------------------

class TestSkipCostFloorDust:
    def test_all_negative_near_cost_floor(self):
        groups = [
            _make_group(mean_net_bps=-54.8, rejection_reasons=["mean_net_return_not_positive: -54.81 bps"]),
            _make_group(mean_net_bps=-50.1, signal_type="signed_imbalance", lookback_ms=30000, horizon_ms=300000),
        ]
        for g in groups:
            worthy, reason = is_mcpt_worthy_group(
                mean_net_bps=g["mean_net_bps"],
                valid_count=g["valid_count"],
                candidate=g["candidate"],
                rejection_reasons=g["rejection_reasons"],
            )
            assert not worthy, f"Group with net={g['mean_net_bps']} should not be MCPT-worthy"

    def test_select_returns_empty_for_all_dust(self):
        groups = [_make_group(mean_net_bps=-52.0), _make_group(mean_net_bps=-48.5)]
        result = select_mcpt_candidate_groups(groups)
        assert result == []


# ---------------------------------------------------------------------------
# Test 2: Select a positive post-cost candidate
# ---------------------------------------------------------------------------

class TestSelectPositiveCandidate:
    def test_positive_net_bps_is_worthy(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=15.0,
            median_net_bps=10.0,
            win_rate=0.55,
            valid_count=100,
        )
        assert ok
        assert "positive_net_bps" in reason

    def test_candidate_flag_overrides(self):
        """A candidate=True group is always MCPT-worthy regardless of stats."""
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=-5.0,
            valid_count=30,
            candidate=True,
        )
        assert ok
        assert "candidate_group" in reason

    def test_selects_best_positive_group(self):
        groups = [
            _make_group(mean_net_bps=-50.0, signal_type="notional_burst", lookback_ms=1000, horizon_ms=1000),
            _make_group(mean_net_bps=10.0, signal_type="signed_imbalance", lookback_ms=10000, horizon_ms=300000),
            _make_group(mean_net_bps=25.0, signal_type="signed_imbalance", lookback_ms=30000, horizon_ms=300000, valid_count=50),
        ]
        selected = select_mcpt_candidate_groups(groups, max_groups=3)
        assert len(selected) >= 2
        # Best should come first
        assert selected[0]["mean_net_bps"] == 25.0


# ---------------------------------------------------------------------------
# Test 3: Near-breakeven group only if it beats baseline
# ---------------------------------------------------------------------------

class TestNearBreakevenBaseline:
    def test_near_breakeven_beats_baseline(self):
        """A group at -5 bps with baseline at -55 bps should be MCPT-worthy."""
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=-5.0,
            median_net_bps=-3.0,
            win_rate=0.48,
            baseline_mean_net_bps=-55.0,
            baseline_win_rate=0.02,
            valid_count=100,
            cost_floor_bps=50.0,
        )
        assert ok
        assert "near_breakeven" in reason or "beats_baseline" in reason

    def test_near_breakeven_doesnt_beat_baseline(self):
        """A group at -10 bps with baseline at -12 bps — does NOT beat baseline by margin."""
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=-10.0,
            median_net_bps=-10.0,
            win_rate=0.3,
            baseline_mean_net_bps=-12.0,
            baseline_win_rate=0.28,
            valid_count=100,
            cost_floor_bps=50.0,
        )
        # -10 > -12 + 5? No: -10 > -7 is false
        assert not ok

    def test_no_baseline_means_not_worthy_if_negative(self):
        ok, _ = is_mcpt_worthy_group(
            mean_net_bps=-8.0,
            valid_count=100,
        )
        assert not ok


# ---------------------------------------------------------------------------
# Test 4: Reject groups below min_events
# ---------------------------------------------------------------------------

class TestMinEventsGate:
    def test_below_min_events(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=20.0,
            valid_count=10,
            min_events=30,
        )
        assert not ok
        assert "min_events" in reason

    def test_at_min_events(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=20.0,
            valid_count=30,
            min_events=30,
        )
        assert ok

    def test_zero_count(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=5.0,
            valid_count=0,
        )
        assert not ok


# ---------------------------------------------------------------------------
# Test 5: NaN / inf safety
# ---------------------------------------------------------------------------

class TestNanInfSafety:
    def test_nan_mean_net(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=float("nan"),
            valid_count=100,
        )
        assert not ok
        assert "not finite" in reason

    def test_inf_mean_net(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=float("inf"),
            valid_count=100,
        )
        assert not ok
        assert "not finite" in reason

    def test_nan_median_net(self):
        ok, reason = is_mcpt_worthy_group(
            mean_net_bps=5.0,
            median_net_bps=float("nan"),
            valid_count=100,
        )
        assert not ok
        assert "not finite" in reason

    def test_none_values(self):
        """When key stats are None, should default to not-worthy."""
        ok, reason = is_mcpt_worthy_group()
        assert not ok

    def test_select_handles_nan_groups(self):
        """select_mcpt_candidate_groups should skip NaN groups."""
        groups = [
            _make_group(mean_net_bps=float("nan")),
            _make_group(mean_net_bps=10.0),
        ]
        selected = select_mcpt_candidate_groups(groups)
        assert len(selected) == 1
        assert selected[0]["mean_net_bps"] == 10.0


# ---------------------------------------------------------------------------
# Test 6: Export clean candidate CSV with expected fields
# ---------------------------------------------------------------------------

class TestExportCsv:
    def test_export_produces_csv(self, tmp_path):
        """End-to-end: create mock report data, export, check CSV."""
        report_dir = tmp_path / "report"
        report_dir.mkdir()

        # Create summary.json
        summary = {
            "capture_dir": "data/test_capture",
            "results_by_group": [
                _make_group(
                    mean_net_bps=15.0,
                    signal_type="signed_imbalance",
                    lookback_ms=10000,
                    horizon_ms=300000,
                    candidate=True,
                    valid_count=60,
                ),
            ],
        }
        with open(report_dir / "summary.json", "w") as f:
            json.dump(summary, f)

        # Create signals.jsonl
        sig1 = {
            "signal_id": "sig-001",
            "ts_event": 1778777469597000000,
            "source_venue": "binance_perp",
            "source_symbol": "BTC/USD",
            "target_venue": "kraken",
            "target_symbol": "BTC/USD",
            "asset": "BTC",
            "signal_type": "trade_flow_impulse",
            "direction": "long",
            "lookback_ms": 10000,
            "threshold_bps": 0.0,
            "source_move_bps": 0.0,
            "source_start_price": 103000.0,
            "source_end_price": 103100.0,
            "strength": 3.5,
            "metadata": {
                "flow_signal_type": "signed_imbalance",
                "lookback_ms": 10000,
                "oi_bucket": "price_up_oi_up",
            },
        }
        sig2 = {
            "signal_id": "sig-002",
            "ts_event": 1778777470000000000,
            "source_venue": "binance_perp",
            "source_symbol": "BTC/USD",
            "target_venue": "kraken",
            "target_symbol": "BTC/USD",
            "asset": "BTC",
            "signal_type": "trade_flow_impulse",
            "direction": "short",
            "lookback_ms": 10000,
            "threshold_bps": 0.0,
            "source_move_bps": 5.0,
            "source_start_price": 103100.0,
            "source_end_price": 103090.0,
            "strength": 2.1,
            "metadata": {
                "flow_signal_type": "signed_imbalance",
                "lookback_ms": 10000,
                "oi_bucket": "price_down_oi_up",
            },
        }
        with open(report_dir / "signals.jsonl", "w") as f:
            f.write(json.dumps(sig1) + "\n")
            f.write(json.dumps(sig2) + "\n")

        # Create forward_returns.jsonl
        fr1 = {
            "signal_id": "sig-001",
            "signal_ts": 1778777469597000000,
            "target_venue": "kraken",
            "target_symbol": "BTC/USD",
            "horizon_ms": 300000,
            "entry_reference_price": 103000.0,
            "forward_price": 103200.0,
            "raw_return_bps": 19.4,
            "direction_adjusted_return_bps": 19.4,
            "fee_bps": 40.0,
            "slippage_bps": 5.0,
            "quote_mismatch_buffer_bps": 5.0,
            "net_return_bps": -30.6,
            "valid": True,
            "rejection_reason": None,
        }
        # Non-matching horizon — should be excluded
        fr2_wrong_hz = {
            "signal_id": "sig-001",
            "signal_ts": 1778777469597000000,
            "target_venue": "kraken",
            "target_symbol": "BTC/USD",
            "horizon_ms": 1000,
            "entry_reference_price": 103000.0,
            "forward_price": 103010.0,
            "raw_return_bps": 1.0,
            "fee_bps": 40.0,
            "slippage_bps": 5.0,
            "quote_mismatch_buffer_bps": 5.0,
            "net_return_bps": -49.0,
            "valid": True,
            "rejection_reason": None,
        }
        fr3 = {
            "signal_id": "sig-002",
            "signal_ts": 1778777470000000000,
            "target_venue": "kraken",
            "target_symbol": "BTC/USD",
            "horizon_ms": 300000,
            "entry_reference_price": 103100.0,
            "forward_price": 103400.0,
            "raw_return_bps": 29.1,
            "direction_adjusted_return_bps": 29.1,
            "fee_bps": 40.0,
            "slippage_bps": 5.0,
            "quote_mismatch_buffer_bps": 5.0,
            "net_return_bps": -20.9,
            "valid": True,
            "rejection_reason": None,
        }
        with open(report_dir / "forward_returns.jsonl", "w") as f:
            f.write(json.dumps(fr1) + "\n")
            f.write(json.dumps(fr2_wrong_hz) + "\n")
            f.write(json.dumps(fr3) + "\n")

        # Export
        candidate = _make_group(
            mean_net_bps=15.0,
            signal_type="signed_imbalance",
            lookback_ms=10000,
            horizon_ms=300000,
            candidate=True,
            valid_count=60,
        )

        out_path = export_mcpt_candidate_series(
            report_dir=report_dir,
            candidate_group=candidate,
        )

        assert out_path.exists()
        # Read CSV and check structure
        with open(out_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2  # Only the matching horizon=300000 rows
        assert "event_timestamp_ns" in rows[0]
        assert "net_return_bps" in rows[0]
        assert "signal_type" in rows[0]
        assert "flow_signal_type" in rows[0]
        assert "oi_bucket" in rows[0]
        assert rows[0]["flow_signal_type"] == "signed_imbalance"
        assert rows[0]["horizon_ms"] == "300000"

    def test_export_summary_json(self, tmp_path):
        report_dir = tmp_path / "report"
        report_dir.mkdir()
        with open(report_dir / "summary.json", "w") as f:
            json.dump({"results_by_group": []}, f)

        out_path = export_mcpt_summary(
            report_dir=report_dir,
            selected_groups=[],
            all_groups=[],
            skipped_reason="no_candidate_signal_to_falsify",
        )
        assert out_path.exists()
        with open(out_path) as f:
            data = json.load(f)
        assert data["mcpt_skipped"] is True
        assert "no_candidate" in data["skip_reason"]


# ---------------------------------------------------------------------------
# Test 7: Select at most max_groups
# ---------------------------------------------------------------------------

class TestMaxGroups:
    def test_respects_max_groups(self):
        groups = [
            _make_group(mean_net_bps=30.0, signal_type="a", lookback_ms=1000),
            _make_group(mean_net_bps=20.0, signal_type="b", lookback_ms=2000),
            _make_group(mean_net_bps=10.0, signal_type="c", lookback_ms=3000),
            _make_group(mean_net_bps=5.0, signal_type="d", lookback_ms=4000),
        ]
        selected = select_mcpt_candidate_groups(groups, max_groups=2)
        assert len(selected) <= 2

    def test_max_groups_one(self):
        groups = [
            _make_group(mean_net_bps=30.0, signal_type="a", lookback_ms=1000),
            _make_group(mean_net_bps=20.0, signal_type="b", lookback_ms=2000),
        ]
        selected = select_mcpt_candidate_groups(groups, max_groups=1)
        assert len(selected) == 1
        assert selected[0]["mean_net_bps"] == 30.0


# ---------------------------------------------------------------------------
# Test 8: No duplicate variants (same signal_type + lookback)
# ---------------------------------------------------------------------------

class TestNoDuplicateVariants:
    def test_deduplicates_same_signal_lookback(self):
        """Three horizons of the same signal_type/lookback — only best horizon selected."""
        groups = [
            _make_group(mean_net_bps=5.0, signal_type="signed_imbalance", lookback_ms=10000, horizon_ms=1000),
            _make_group(mean_net_bps=15.0, signal_type="signed_imbalance", lookback_ms=10000, horizon_ms=300000),
            _make_group(mean_net_bps=8.0, signal_type="signed_imbalance", lookback_ms=10000, horizon_ms=60000),
        ]
        selected = select_mcpt_candidate_groups(groups, max_groups=3)
        # Should only have 1 group — the best horizon for signed_imbalance/10000ms
        assert len(selected) == 1
        assert selected[0]["horizon_ms"] == 300000  # Best mean_net_bps

    def test_different_signals_different_groups(self):
        """Different signal types should both be selected."""
        groups = [
            _make_group(mean_net_bps=15.0, signal_type="notional_burst", lookback_ms=1000, horizon_ms=5000),
            _make_group(mean_net_bps=10.0, signal_type="signed_imbalance", lookback_ms=10000, horizon_ms=300000),
        ]
        selected = select_mcpt_candidate_groups(groups, max_groups=3)
        assert len(selected) == 2
        types = {g["signal_type"] for g in selected}
        assert types == {"notional_burst", "signed_imbalance"}


# ---------------------------------------------------------------------------
# Utility: slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("binance_perp-kraken-signed_imbalance-10000ms") == "binance_perp_kraken_signed_imbalance_10000ms"

    def test_special_chars(self):
        assert _slugify("BTC/USD") == "btc_usd"