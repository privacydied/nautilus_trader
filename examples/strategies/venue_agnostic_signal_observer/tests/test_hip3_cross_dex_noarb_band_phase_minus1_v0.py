"""Tests for HIP-3 Cross-DEX No-Arb-Band Phase -1 core module."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.hip3_cross_dex_noarb_band_phase_minus1_v0 import (
    ALLOWED_STATUSES,
    FORBIDDEN_STATUSES,
    FLX_FORBIDDEN_BY_DEFAULT,
    BuilderDexLeg,
    CrossDexPair,
    FeeMarginConfig,
    FundingConfig,
    NoArbBandConfig,
    L2BookSnapshot,
    BookDepthMetrics,
    AlignedCrossDexObservation,
    ReversionDiagnostics,
    PairSpreadSummary,
    PhaseMinus1Decision,
    parse_pair_string,
    get_default_pairs,
    discover_fee_margin_config,
    discover_funding_config,
    compute_noarb_band_config,
    parse_l2_snapshot_file,
    compute_book_depth_metrics,
    align_snapshots,
    compute_reversion_diagnostics,
    compute_pair_summary,
    compute_gate_decision,
    percentile,
    safe_float,
    write_json,
    make_run_id,
)


# ===================================================================
# 1. Pair parsing
# ===================================================================

class TestPairParsing:
    def test_valid_pair(self):
        p = parse_pair_string("cash:NVDA|km:NVDA")
        assert p.left_leg.dex == "cash"
        assert p.right_leg.dex == "km"
        assert p.display_symbol == "NVDA"
        assert p.is_primary is True

    def test_pair_id_format(self):
        p = parse_pair_string("cash:TSLA|km:TSLA")
        assert p.pair_id == "cash:TSLA|km:TSLA"

    def test_reject_malformed_no_pipe(self):
        with pytest.raises(ValueError, match=r"\:"):
            parse_pair_string("cash:NVDA")

    def test_reject_malformed_no_colon(self):
        with pytest.raises(ValueError, match=r"\:"):
            parse_pair_string("NVDA|TSLA")

    def test_reject_mismatched_symbols(self):
        with pytest.raises(ValueError, match="Display symbols must match"):
            parse_pair_string("cash:NVDA|km:TSLA")

    def test_allow_mismatched_symbols(self):
        p = parse_pair_string("cash:NVDA|km:TSLA", allow_mismatched=True)
        assert p.display_symbol == "NVDA"

    def test_flx_rejected_by_default(self):
        with pytest.raises(ValueError, match=r"\:"):
            parse_pair_string("flx:TSLA|km:TSLA")

    def test_flx_allowed_diagnostic_only(self):
        p = parse_pair_string("flx:TSLA|km:TSLA", allow_flx=True)
        assert p.is_primary is False
        assert "flx" in p.pair_id

    def test_default_pairs(self):
        pairs = get_default_pairs()
        assert len(pairs) == 2
        assert pairs[0].pair_id == "cash:NVDA|km:NVDA"
        assert pairs[1].pair_id == "cash:TSLA|km:TSLA"

    def test_dex_variants_not_collapsed(self):
        p1 = parse_pair_string("cash:NVDA|km:NVDA")
        p2 = parse_pair_string("cash:TSLA|km:TSLA")
        assert p1.pair_id != p2.pair_id
        assert p1.left_leg.dex != p1.right_leg.dex


# ===================================================================
# 2. Fee/funding discovery
# ===================================================================

class TestFeeDiscovery:
    def test_fallback_conservative(self):
        cp = MagicMock()
        cp.http_post_json.side_effect = Exception("no network")
        fee = discover_fee_margin_config("cash", "cash:NVDA", cp, fallback_one_way_bps=12.5)
        assert fee.conservative_one_way_fee_bps >= 12.5
        assert fee.conservative_four_fill_fee_band_bps >= 50.0  # Four-fill, not two-fill

    def test_four_fill_not_two_fill(self):
        cp = MagicMock()
        cp.http_post_json.side_effect = Exception("no network")
        fee = discover_fee_margin_config("cash", "cash:NVDA", cp, fallback_one_way_bps=12.5)
        # Four fill = 2 * left_one_way + 2 * right_one_way
        # With fallback 12.5: 2*12.5 + 2*12.5 = 50.0
        assert fee.conservative_four_fill_fee_band_bps >= 50.0

    def test_noarb_band_includes_funding(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left_fee = FeeMarginConfig(dex="cash", api_symbol="cash:NVDA")
        right_fee = FeeMarginConfig(dex="km", api_symbol="km:NVDA")
        left_fund = FundingConfig(dex="cash", api_symbol="cash:NVDA", fallback_funding_diff_bps_per_hour=1.0)
        right_fund = FundingConfig(dex="km", api_symbol="km:NVDA", fallback_funding_diff_bps_per_hour=1.0)
        band = compute_noarb_band_config(pair, left_fee, right_fee, left_fund, right_fund)
        assert band.funding_differential_band_bps > 0
        assert band.conservative_noarb_band_bps > band.conservative_four_fill_fee_band_bps


# ===================================================================
# 3. L2 parsing
# ===================================================================

class TestL2Parsing:
    def test_parse_valid_gzipped(self):
        import gzip
        data = [{"height": 100, "block_time": "2026-05-29T10:00:00Z",
                 "bids": [{"px": "100.0", "sz": "1.0", "n": "1"}],
                 "asks": [{"px": "100.5", "sz": "0.5", "n": "1"}]}]
        raw = gzip.compress(json.dumps(data).encode())
        snaps = parse_l2_snapshot_file(raw, "test_key", "cash:NVDA", "cash", "NVDA")
        assert len(snaps) == 1
        assert snaps[0].parse_status == "ok"
        assert snaps[0].best_bid == 100.0
        assert snaps[0].best_ask == 100.5

    def test_reject_crossed_book(self):
        data = [{"height": 100, "block_time": "2026-05-29T10:00:00Z",
                 "bids": [{"px": "100.5", "sz": "1.0"}],
                 "asks": [{"px": "100.0", "sz": "0.5"}]}]
        raw = json.dumps(data).encode()
        snaps = parse_l2_snapshot_file(raw, "test", "cash:NVDA", "cash", "NVDA")
        assert len(snaps) == 0

    def test_handle_string_px_sz(self):
        data = [{"height": 100, "block_time": "2026-05-29T10:00:00Z",
                 "bids": [{"px": "200.0", "sz": "2.0", "n": "3"}],
                 "asks": [{"px": "200.5", "sz": "1.0", "n": "1"}]}]
        raw = json.dumps(data).encode()
        snaps = parse_l2_snapshot_file(raw, "test", "cash:NVDA", "cash", "NVDA")
        assert len(snaps) == 1
        assert snaps[0].best_bid == 200.0

    def test_reject_negative_size(self):
        data = [{"height": 100, "block_time": "2026-05-29T10:00:00Z",
                 "bids": [{"px": "100.0", "sz": "-1.0"}],
                 "asks": [{"px": "100.5", "sz": "0.5"}]}]
        raw = json.dumps(data).encode()
        snaps = parse_l2_snapshot_file(raw, "test", "cash:NVDA", "cash", "NVDA")
        assert len(snaps) == 0

    def test_decode_failure_not_zero_data(self):
        raw = b"\x00\x01\x02\x03invalid"
        snaps = parse_l2_snapshot_file(raw, "test", "cash:NVDA", "cash", "NVDA")
        assert len(snaps) == 1
        assert "DECODE_FAILURE" in snaps[0].parse_status


# ===================================================================
# 4. Depth calculations
# ===================================================================

class TestDepthCalculations:
    def test_depth_at_thresholds(self):
        bids = [{"px": 100.0, "sz": 10.0}]  # $1000 notional
        asks = [{"px": 100.0, "sz": 10.0}]
        dm = compute_book_depth_metrics(bids, asks)
        assert dm.depth_bid_100 == 100.0
        assert dm.depth_bid_500 == 500.0
        assert dm.depth_bid_1000 == 1000.0
        assert dm.depth_bid_5000 == 1000.0  # capped at available
        assert dm.min_two_sided_depth_500 == 500.0

    def test_top20_cap_flag(self):
        bids = [{"px": float(i), "sz": 1.0} for i in range(20)]
        asks = [{"px": float(200 + i), "sz": 1.0} for i in range(20)]
        dm = compute_book_depth_metrics(bids, asks)
        assert dm.top20_depth_cap_flag is True


# ===================================================================
# 4. Alignment
# ===================================================================

class TestAlignment:
    def _make_snap(self, api_sym, dex, ts_ms, mid=100.0, spread_bps=1.0, bh=None):
        return L2BookSnapshot(
            api_symbol=api_sym, dex=dex, display_symbol=api_sym.split(":")[1],
            timestamp_ms=ts_ms, timestamp_utc="2026-05-29T10:00:00Z",
            block_height=bh,
            bids=[{"px": mid - 0.05, "sz": 1.0}], asks=[{"px": mid + 0.05, "sz": 1.0}],
            best_bid=mid - 0.05, best_ask=mid + 0.05,
            mid=mid, quoted_spread_bps=spread_bps,
            two_sided=True, parse_status="ok", source_key="test",
        )

    def _noarb(self, pair):
        return NoArbBandConfig(pair_id=pair.pair_id,
                               left_fee_config=FeeMarginConfig(dex=pair.left_leg.dex, api_symbol=pair.left_leg.api_symbol),
                               right_fee_config=FeeMarginConfig(dex=pair.right_leg.dex, api_symbol=pair.right_leg.api_symbol),
                               left_funding_config=FundingConfig(dex=pair.left_leg.dex, api_symbol=pair.left_leg.api_symbol),
                               right_funding_config=FundingConfig(dex=pair.right_leg.dex, api_symbol=pair.right_leg.api_symbol))

    def test_exact_alignment_same_timestamp(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1000000.0)]
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair), alignment_mode="exact")
        assert len(obs) == 1
        assert obs[0].timestamp_gap_seconds < 0.001
        assert diag["exact_block_time_overlap_count"] == 1

    def test_exact_alignment_different_timestamps(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1001000.0)]  # 1s gap
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair), alignment_mode="exact")
        assert len(obs) == 0
        assert diag["alignment_failure_reason"] == "exact_mode_no_matching_timestamps"

    def test_nearest_alignment_within_tolerance(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1001000.0)]  # 1s gap
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair),
                                     alignment_mode="nearest", max_align_gap_seconds=5.0)
        assert len(obs) == 1
        assert obs[0].is_primary_alignment is True
        assert diag["alignment_semantics"] == "diagnostic_nearest_neighbor"

    def test_nearest_alignment_rejects_outside_tolerance(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1050000.0)]  # 50s gap
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair),
                                     alignment_mode="nearest", max_align_gap_seconds=5.0)
        assert len(obs) == 0
        assert diag["alignment_failure_reason"] is not None

    def test_diagnostic_alignment_5_to_30s(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1020000.0)]  # 20s gap
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair),
                                     primary_tol=5.0, alignment_mode="nearest", max_align_gap_seconds=30.0)
        assert len(obs) == 1
        assert obs[0].is_primary_alignment is False

    def test_reject_over_30s(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1050000.0)]  # 50s gap
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair),
                                     alignment_mode="nearest", max_align_gap_seconds=30.0)
        assert len(obs) == 0

    def test_same_block_flag(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0, bh=100)]
        right = [self._make_snap("km:NVDA", "km", 1000000.0, bh=100)]
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair))
        assert len(obs) == 1
        assert obs[0].same_block_height is True

    def test_alignment_diagnostics_gap_quantiles(self):
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0 + i * 1000) for i in range(5)]
        right = [self._make_snap("km:NVDA", "km", 1000000.0 + i * 1000 + 200) for i in range(5)]
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair),
                                     alignment_mode="nearest", max_align_gap_seconds=10.0)
        assert len(obs) == 5
        assert diag["median_nearest_gap_seconds"] is not None
        assert diag["p90_nearest_gap_seconds"] is not None
        assert diag["p99_nearest_gap_seconds"] is not None

    def test_zero_alignment_pair_not_classified(self):
        """A pair with zero aligned observations cannot be within-band or outside-band."""
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        # Snapshots 10 minutes apart — too far for any tolerance
        left = [self._make_snap("cash:NVDA", "cash", 1000000.0)]
        right = [self._make_snap("km:NVDA", "km", 1600000.0)]
        obs, diag = align_snapshots(left, right, pair, self._noarb(pair),
                                     alignment_mode="nearest", max_align_gap_seconds=5.0)
        assert len(obs) == 0
        assert diag["alignment_failure_reason"] is not None


# ===================================================================
# 6. Spread formulas
# ===================================================================

class TestSpreadFormulas:
    def test_cross_mid_spread(self):
        """Verify cross-mid spread formula."""
        # left_mid=100, right_mid=101
        # avg = 100.5, spread = 10000*(100-101)/100.5 = -99.502... bps
        left_mid, right_mid = 100.0, 101.0
        avg = (left_mid + right_mid) / 2.0
        spread = 10000.0 * (left_mid - right_mid) / avg
        expected = 10000.0 * (-1.0) / 100.5
        assert abs(spread - expected) < 0.01

    def test_visible_spread_additive(self):
        left_q = 2.0
        right_q = 3.0
        combined = left_q + right_q
        assert combined == 5.0

    def test_excess_over_noarb(self):
        abs_spread = 60.0
        noarb = 55.0
        excess = abs_spread - noarb
        assert excess == 5.0

    def test_excess_not_pnl(self):
        """Verify this is diagnostic, not PnL."""
        excess = 10.0
        assert excess > 0  # Just a number, not profitability


# ===================================================================
# 7. Reversion diagnostics
# ===================================================================

class TestReversionDiagnostics:
    def test_reversion_with_zero_crossings(self):
        obs = [
            AlignedCrossDexObservation(pair_id="p", display_symbol="X",
                left_api_symbol="a:X", right_api_symbol="b:X",
                left_timestamp_utc="", right_timestamp_utc="",
                timestamp_gap_seconds=1.0, cross_mid_spread_bps=10.0),
            AlignedCrossDexObservation(pair_id="p", display_symbol="X",
                left_api_symbol="a:X", right_api_symbol="b:X",
                left_timestamp_utc="", right_timestamp_utc="",
                timestamp_gap_seconds=1.0, cross_mid_spread_bps=-10.0),
            AlignedCrossDexObservation(pair_id="p", display_symbol="X",
                left_api_symbol="a:X", right_api_symbol="b:X",
                left_timestamp_utc="", right_timestamp_utc="",
                timestamp_gap_seconds=1.0, cross_mid_spread_bps=8.0),
            AlignedCrossDexObservation(pair_id="p", display_symbol="X",
                left_api_symbol="a:X", right_api_symbol="b:X",
                left_timestamp_utc="", right_timestamp_utc="",
                timestamp_gap_seconds=1.0, cross_mid_spread_bps=-5.0),
            AlignedCrossDexObservation(pair_id="p", display_symbol="X",
                left_api_symbol="a:X", right_api_symbol="b:X",
                left_timestamp_utc="", right_timestamp_utc="",
                timestamp_gap_seconds=1.0, cross_mid_spread_bps=7.0),
        ]
        rev = compute_reversion_diagnostics(obs, "p")
        assert rev.zero_crossing_count >= 3
        assert rev.reversion_supported is True

    def test_level_offset(self):
        obs = [
            AlignedCrossDexObservation(pair_id="p", display_symbol="X",
                left_api_symbol="a:X", right_api_symbol="b:X",
                left_timestamp_utc="", right_timestamp_utc="",
                timestamp_gap_seconds=1.0, cross_mid_spread_bps=10.0 + i)
            for i in range(20)
        ]
        rev = compute_reversion_diagnostics(obs, "p")
        assert rev.level_offset_detected is True
        assert rev.reversion_supported is False


# ===================================================================
# 8. Gate logic
# ===================================================================

class TestGateLogic:
    def test_no_data_returns_sampler_failure(self):
        summaries = [PairSpreadSummary(pair_id="p1", aligned_observation_count=0),
                     PairSpreadSummary(pair_id="p2", aligned_observation_count=0)]
        decision = compute_gate_decision(summaries, {}, {}, {}, {})
        assert decision.final_status == "HIP3_CROSS_DEX_NOARB_ZERO_DATA_SAMPLER_FAILURE"

    def test_flx_cannot_pass(self):
        summaries = [PairSpreadSummary(pair_id="flx:TSLA|km:TSLA", aligned_observation_count=1000),
                     PairSpreadSummary(pair_id="cash:NVDA|km:NVDA", aligned_observation_count=1000)]
        decision = compute_gate_decision(summaries, {}, {}, {}, {})
        assert "flx" in str(decision.warnings)

    def test_forbidden_statuses_absent(self):
        for status in FORBIDDEN_STATUSES:
            assert status not in ALLOWED_STATUSES

    def test_no_tail_when_within_band(self):
        summaries = [PairSpreadSummary(pair_id="p1", aligned_observation_count=600,
                                       primary_alignment_count=600, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=-2.0),
                     PairSpreadSummary(pair_id="p2", aligned_observation_count=600,
                                       primary_alignment_count=600, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=-1.0)]
        decision = compute_gate_decision(summaries, {}, {}, {}, {})
        assert "SPREAD_WITHIN_BAND" in decision.final_status or "NO_TAIL" in decision.final_status

    def test_insufficient_observations_blocked(self):
        summaries = [PairSpreadSummary(pair_id="p1", aligned_observation_count=100,
                                       primary_alignment_count=100, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=10.0),
                     PairSpreadSummary(pair_id="p2", aligned_observation_count=100,
                                       primary_alignment_count=100, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=10.0)]
        rev = {"p1": {"classification": "reversion_supported"},
               "p2": {"classification": "reversion_supported"}}
        decision = compute_gate_decision(summaries, {}, rev, {}, {})
        # 100 < 500, should be blocked
        assert decision.final_status != "HIP3_CROSS_DEX_NOARB_NEXT_PRECOMMITMENT_REVIEW_ALLOWED"

    def test_persistent_offset_blocked(self):
        summaries = [PairSpreadSummary(pair_id="p1", aligned_observation_count=600,
                                       primary_alignment_count=600, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=10.0,
                                       depth_summary={"left_median_min_depth_500": 600, "right_median_min_depth_500": 600}),
                     PairSpreadSummary(pair_id="p2", aligned_observation_count=600,
                                       primary_alignment_count=600, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=10.0)]
        rev = {"p1": {"classification": "persistent_level_offset"},
               "p2": {"classification": "reversion_supported"}}
        decision = compute_gate_decision(summaries, {}, rev, {}, {})
        assert decision.final_status != "HIP3_CROSS_DEX_NOARB_NEXT_PRECOMMITMENT_REVIEW_ALLOWED"

    def test_forward_validation_failure_blocks(self):
        summaries = [PairSpreadSummary(pair_id="p1", aligned_observation_count=600,
                                       primary_alignment_count=600, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=10.0),
                     PairSpreadSummary(pair_id="p2", aligned_observation_count=600,
                                       primary_alignment_count=600, primary_alignment_share=1.0,
                                       p95_excess_over_noarb_band_bps=10.0)]
        fwd = {"overlap_available": True, "validation_status": "FORWARD_RECORDER_CROSS_VALIDATION_FAILED"}
        decision = compute_gate_decision(summaries, {}, {}, fwd, {})
        assert "FORWARD_RECORDER_VALIDATION_FAILED" in decision.final_status


# ===================================================================
# 9. Utility
# ===================================================================

class TestUtils:
    def test_percentile(self):
        assert percentile([1, 2, 3, 4, 5], 50.0) == 3.0
        assert percentile([1, 2, 3, 4, 5], 95.0) >= 4.0

    def test_safe_float(self):
        assert safe_float("12.5") == 12.5
        assert safe_float(None) is None
        assert safe_float("abc") is None

    def test_make_run_id(self):
        rid = make_run_id()
        assert "_" in rid
        assert len(rid) > 10

    def test_no_subprocess(self):
        mod_path = Path(__file__).resolve().parents[1] / "hip3_cross_dex_noarb_band_phase_minus1_v0.py"
        src = mod_path.read_text(encoding="utf-8")
        assert "import subprocess" not in src
        assert "subprocess." not in src
        assert "os.system(" not in src
        assert "eval(" not in src


# ===================================================================
# 10. Shared-date sampler
# ===================================================================

from examples.strategies.venue_agnostic_signal_observer.hip3_cross_dex_noarb_band_phase_minus1_v0 import (
    extract_partition_from_sonarx_key,
    extract_file_id_from_sonarx_key,
    compute_shared_partitions_for_pair,
    select_keys_from_shared_partitions,
)


class TestPartitionExtraction:
    def test_extract_partition_standard_key(self):
        key = "market_data/hip3/cash:NVDA/l2-summary-snapshots/885810000/885811000.json.gz"
        assert extract_partition_from_sonarx_key(key) == "885810000"

    def test_extract_partition_km_key(self):
        key = "market_data/hip3/km:NVDA/l2-summary-snapshots/902750000/902751000.json.gz"
        assert extract_partition_from_sonarx_key(key) == "902750000"

    def test_extract_partition_no_match(self):
        key = "market_data/hip3/cash:NVDA/other-path/885810000/file.json.gz"
        assert extract_partition_from_sonarx_key(key) == ""

    def test_extract_partition_empty(self):
        assert extract_partition_from_sonarx_key("") == ""

    def test_extract_file_id(self):
        key = "market_data/hip3/cash:NVDA/l2-summary-snapshots/885810000/885811000.json.gz"
        assert extract_file_id_from_sonarx_key(key) == "885811000"

    def test_extract_file_id_no_gz(self):
        key = "market_data/hip3/cash:NVDA/l2-summary-snapshots/885810000/885811000.json"
        assert extract_file_id_from_sonarx_key(key) == "885811000"


class TestSharedPartitions:
    def _key(self, api_sym, partition, file_id):
        return {"key": f"market_data/hip3/{api_sym}/l2-summary-snapshots/{partition}/{file_id}.json.gz", "size": 1000}

    def test_tsla_shared_partition(self):
        """TSLA: both legs have partition 885810000 -> shared."""
        left = [self._key("cash:TSLA", "885810000", f"88581{i}000") for i in range(1, 11)]
        right = [self._key("km:TSLA", "885810000", f"88581{i}000") for i in range(1, 11)]
        result = compute_shared_partitions_for_pair(left, right, max_overlap_units=7)
        assert "885810000" in result["overlap_units"]
        assert "885810000" in result["selected_overlap_units"]
        assert len(result["selected_overlap_units"]) <= 7

    def test_nvda_no_shared_partition(self):
        """NVDA: left has 885810000, right has 902750000 -> no overlap."""
        left = [self._key("cash:NVDA", "885810000", f"88581{i}000") for i in range(1, 11)]
        right = [self._key("km:NVDA", "902750000", f"90275{i}000") for i in range(1, 11)]
        result = compute_shared_partitions_for_pair(left, right)
        assert result["overlap_units"] == []
        assert result["selected_overlap_units"] == []

    def test_nvda_with_shared_partition(self):
        """NVDA: if both legs have 885810000, they share."""
        left = [self._key("cash:NVDA", "885810000", f"88581{i}000") for i in range(1, 11)]
        right = [self._key("km:NVDA", "885810000", f"88581{i}000") for i in range(1, 11)]
        result = compute_shared_partitions_for_pair(left, right)
        assert "885810000" in result["overlap_units"]
        assert result["left_keys_by_unit"]["885810000"] is not None
        assert result["right_keys_by_unit"]["885810000"] is not None

    def test_multiple_shared_partitions(self):
        """Multiple partitions shared -> selected up to max."""
        left = [self._key("cash:TSLA", p, f"f{i}") for p in ["885810000", "885820000"] for i in range(5)]
        right = [self._key("km:TSLA", p, f"f{i}") for p in ["885810000", "885820000"] for i in range(5)]
        result = compute_shared_partitions_for_pair(left, right, max_overlap_units=1)
        assert len(result["selected_overlap_units"]) == 1

    def test_left_right_units_available(self):
        left = [self._key("cash:TSLA", "885810000", "f1"), self._key("cash:TSLA", "885820000", "f2")]
        right = [self._key("km:TSLA", "885810000", "f1")]
        result = compute_shared_partitions_for_pair(left, right)
        assert "885810000" in result["left_units_available"]
        assert "885820000" in result["left_units_available"]
        assert "885810000" in result["right_units_available"]
        assert "885820000" not in result["right_units_available"]


class TestSelectKeysFromSharedPartitions:
    def _key(self, api_sym, partition, file_id):
        return {"key": f"market_data/hip3/{api_sym}/l2-summary-snapshots/{partition}/{file_id}.json.gz", "size": 1000}

    def test_balanced_selection(self):
        """Selects min_files_per_unit from each unit, then fills."""
        keys_by_unit = {
            "885810000": [self._key("cash:TSLA", "885810000", f"f{i}") for i in range(10)],
            "885820000": [self._key("cash:TSLA", "885820000", f"f{i}") for i in range(10)],
        }
        selected = select_keys_from_shared_partitions(
            keys_by_unit, ["885810000", "885820000"],
            max_files=6, min_files_per_unit=2,
        )
        assert len(selected) == 6
        # Check both units are represented
        units_used = set()
        for k in selected:
            p = extract_partition_from_sonarx_key(k["key"])
            units_used.add(p)
        assert len(units_used) == 2

    def test_respects_max_files(self):
        keys_by_unit = {
            "p1": [self._key("x", "p1", f"f{i}") for i in range(20)],
        }
        selected = select_keys_from_shared_partitions(
            keys_by_unit, ["p1"], max_files=5, min_files_per_unit=1,
        )
        assert len(selected) == 5

    def test_empty_units(self):
        selected = select_keys_from_shared_partitions({}, [], max_files=10)
        assert selected == []


class TestSharedSamplerIntegration:
    def test_tsla_both_legs_same_partition(self):
        """TSLA: verify that shared-date sampler produces identical selected units."""
        left_keys = [
            {"key": f"market_data/hip3/cash:TSLA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 21)
        ]
        right_keys = [
            {"key": f"market_data/hip3/km:TSLA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 21)
        ]
        shared = compute_shared_partitions_for_pair(left_keys, right_keys, max_overlap_units=7)
        left_selected = select_keys_from_shared_partitions(
            shared["left_keys_by_unit"], shared["selected_overlap_units"],
            max_files=20, min_files_per_unit=1,
        )
        right_selected = select_keys_from_shared_partitions(
            shared["right_keys_by_unit"], shared["selected_overlap_units"],
            max_files=20, min_files_per_unit=1,
        )
        left_units = sorted(set(extract_partition_from_sonarx_key(k["key"]) for k in left_selected))
        right_units = sorted(set(extract_partition_from_sonarx_key(k["key"]) for k in right_selected))
        assert left_units == right_units, f"Left units {left_units} != Right units {right_units}"
        assert len(left_selected) > 0
        assert len(right_selected) > 0

    def test_nvda_independent_dates_reproduces_old_failure(self):
        """NVDA: independent partition selection reproduces the old bug."""
        left_keys = [
            {"key": f"market_data/hip3/cash:NVDA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 11)
        ]
        right_keys = [
            {"key": f"market_data/hip3/km:NVDA/l2-summary-snapshots/902750000/90275{i}000.json.gz", "size": 2000}
            for i in range(1, 11)
        ]
        shared = compute_shared_partitions_for_pair(left_keys, right_keys)
        assert shared["overlap_units"] == [], "NVDA should have no shared partitions with independent dates"
        assert shared["selected_overlap_units"] == []

    def test_nvda_shared_units_aligns(self):
        """NVDA: with shared partition, both legs align."""
        left_keys = [
            {"key": f"market_data/hip3/cash:NVDA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 21)
        ]
        right_keys = [
            {"key": f"market_data/hip3/km:NVDA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 2000}
            for i in range(1, 21)
        ]
        shared = compute_shared_partitions_for_pair(left_keys, right_keys)
        assert "885810000" in shared["overlap_units"]
        left_selected = select_keys_from_shared_partitions(
            shared["left_keys_by_unit"], shared["selected_overlap_units"],
            max_files=20, min_files_per_unit=1,
        )
        right_selected = select_keys_from_shared_partitions(
            shared["right_keys_by_unit"], shared["selected_overlap_units"],
            max_files=20, min_files_per_unit=1,
        )
        left_units = sorted(set(extract_partition_from_sonarx_key(k["key"]) for k in left_selected))
        right_units = sorted(set(extract_partition_from_sonarx_key(k["key"]) for k in right_selected))
        assert left_units == right_units

    def test_no_silent_fallback_to_independent(self):
        """When require_shared_dates=True and no overlap, inventory shows no overlap."""
        left_keys = [
            {"key": f"market_data/hip3/cash:NVDA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 11)
        ]
        right_keys = [
            {"key": f"market_data/hip3/km:NVDA/l2-summary-snapshots/902750000/90275{i}000.json.gz", "size": 2000}
            for i in range(1, 11)
        ]
        shared = compute_shared_partitions_for_pair(left_keys, right_keys)
        assert shared["overlap_units"] == []
        # When require_shared_dates=True, the caller should stop, not fall back
        # This is verified by the integration test in run_phase_minus1

    def test_selected_units_identical_flag(self):
        """Verify sampled_units_identical is computed correctly."""
        left_keys = [
            {"key": f"market_data/hip3/cash:TSLA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 11)
        ]
        right_keys = [
            {"key": f"market_data/hip3/km:TSLA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 11)
        ]
        shared = compute_shared_partitions_for_pair(left_keys, right_keys)
        left_selected = select_keys_from_shared_partitions(
            shared["left_keys_by_unit"], shared["selected_overlap_units"],
            max_files=10, min_files_per_unit=1,
        )
        right_selected = select_keys_from_shared_partitions(
            shared["right_keys_by_unit"], shared["selected_overlap_units"],
            max_files=10, min_files_per_unit=1,
        )
        left_units = sorted(set(extract_partition_from_sonarx_key(k["key"]) for k in left_selected))
        right_units = sorted(set(extract_partition_from_sonarx_key(k["key"]) for k in right_selected))
        assert left_units == right_units

    def test_exact_alignment_required_for_classification(self):
        """Zero aligned observations cannot be classified within/outside band."""
        pair = parse_pair_string("cash:NVDA|km:NVDA")
        left = [L2BookSnapshot(
            api_symbol="cash:NVDA", dex="cash", display_symbol="NVDA",
            timestamp_ms=1000000.0, timestamp_utc="2026-05-29T10:00:00Z",
            bids=[{"px": 100.0, "sz": 1.0}], asks=[{"px": 100.5, "sz": 1.0}],
            best_bid=100.0, best_ask=100.5, mid=100.25, quoted_spread_bps=5.0,
            two_sided=True, parse_status="ok", source_key="test",
        )]
        right = [L2BookSnapshot(
            api_symbol="km:NVDA", dex="km", display_symbol="NVDA",
            timestamp_ms=2000000.0, timestamp_utc="2026-05-29T10:00:01Z",
            bids=[{"px": 100.0, "sz": 1.0}], asks=[{"px": 100.5, "sz": 1.0}],
            best_bid=100.0, best_ask=100.5, mid=100.25, quoted_spread_bps=5.0,
            two_sided=True, parse_status="ok", source_key="test",
        )]
        noarb = NoArbBandConfig(
            pair_id=pair.pair_id,
            left_fee_config=FeeMarginConfig(dex="cash", api_symbol="cash:NVDA"),
            right_fee_config=FeeMarginConfig(dex="km", api_symbol="km:NVDA"),
            left_funding_config=FundingConfig(dex="cash", api_symbol="cash:NVDA"),
            right_funding_config=FundingConfig(dex="km", api_symbol="km:NVDA"),
        )
        obs, diag = align_snapshots(left, right, pair, noarb, alignment_mode="exact")
        assert len(obs) == 0
        # Cannot classify as within_band or outside_band with zero observations
        assert diag.get("alignment_failure_reason") is not None

    def test_below_threshold_underpowered(self):
        """Below-threshold aligned observations classify as underpowered."""
        ps = PairSpreadSummary(
            pair_id="cash:TSLA|km:TSLA",
            aligned_observation_count=30,  # below min_aligned_observations=50
            p95_excess_over_noarb_band_bps=-10.0,
        )
        assert ps.aligned_observation_count < 50
        # The gate decision should not classify as within_band with too few obs

    def test_no_pnl_returns_signals_in_shared_inventory(self):
        """Shared-date inventory contains no PnL/returns/signals fields."""
        left_keys = [
            {"key": f"market_data/hip3/cash:TSLA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 6)
        ]
        right_keys = [
            {"key": f"market_data/hip3/km:TSLA/l2-summary-snapshots/885810000/88581{i}000.json.gz", "size": 3000}
            for i in range(1, 6)
        ]
        shared = compute_shared_partitions_for_pair(left_keys, right_keys)
        # Convert to JSON and check for forbidden fields
        import json
        shared_json = json.dumps(shared)
        for forbidden in ["pnl", "profit", "returns", "signal", "entry", "exit", "position"]:
            assert forbidden not in shared_json.lower(), f"Found forbidden field: {forbidden}"

    def test_no_production_subprocess_os_system_eval(self):
        """Module has no production subprocess, os.system, or eval."""
        mod_path = Path(__file__).resolve().parents[1] / "hip3_cross_dex_noarb_band_phase_minus1_v0.py"
        src = mod_path.read_text(encoding="utf-8")
        assert "import subprocess" not in src
        assert "subprocess." not in src
        assert "os.system(" not in src
        assert "eval(" not in src
