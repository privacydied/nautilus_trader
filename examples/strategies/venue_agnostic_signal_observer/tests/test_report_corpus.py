"""Tests for run_report_corpus module (aggregate_corpus, corpus_config_key, etc.).

Tests:
1. GroupsExactConfigKeys — groups match on (source_venue, target_venue, signal_type, lookback_ms, horizon_ms)
2. DoesNotMergeDifferentConfigs — different horizons/lookbacks produce separate aggregations
3. DoesNotCherryPickBestResultAsPrimary — sort by num_captures desc, not by max mean_net_bps
4. HandlesEmptyReportDirs — empty results_by_group produces empty aggregation list
5. AggregatesMultipleCaptures — two report dirs with matching config produce correct num_captures=2
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from venue_agnostic_signal_observer.run_report_corpus import (
    CorpusAggregation,
    aggregate_corpus,
    corpus_config_key,
    format_corpus_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_group(
    *,
    source_venue: str = "binance_perp",
    target_venue: str = "kraken",
    signal_type: str = "notional_burst",
    lookback_ms: int = 1000,
    horizon_ms: int = 5000,
    mean_net_bps: float = 15.0,
    median_net_bps: float = 12.0,
    win_rate: float = 0.55,
    valid_count: int = 100,
    candidate: bool = False,
) -> dict:
    """Build a mock results_by_group entry."""
    return {
        "source_venue": source_venue,
        "target_venue": target_venue,
        "signal_type": signal_type,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "mean_net_bps": mean_net_bps,
        "median_net_bps": median_net_bps,
        "win_rate": win_rate,
        "valid_count": valid_count,
        "candidate": candidate,
    }


def _write_report_dir(tmp_path: Path, groups: list[dict], idx: int = 0) -> Path:
    """Create a mock report directory with summary.json."""
    report_dir = tmp_path / f"report_{idx}"
    report_dir.mkdir(exist_ok=True)
    summary = {
        "capture_dir": f"data/capture_{idx}",
        "results_by_group": groups,
    }
    with open(report_dir / "summary.json", "w") as f:
        json.dump(summary, f)
    return report_dir


# ---------------------------------------------------------------------------
# Test 1: GroupsExactConfigKeys
# ---------------------------------------------------------------------------

class TestGroupsExactConfigKeys:
    def test_same_config_key_groups(self):
        """Groups with identical config keys belong to the same aggregation."""
        group = _make_group(
            source_venue="binance_perp",
            target_venue="kraken",
            signal_type="notional_burst",
            lookback_ms=1000,
            horizon_ms=5000,
        )
        key = corpus_config_key(group)
        assert key == ("binance_perp", "kraken", "notional_burst", 1000, 5000)

    def test_different_source_venue_separate(self):
        """Different source_venue → different key."""
        g1 = _make_group(source_venue="binance_perp")
        g2 = _make_group(source_venue="coinbase")
        assert corpus_config_key(g1) != corpus_config_key(g2)

    def test_different_horizon_separate(self):
        """Different horizon_ms → different key."""
        g1 = _make_group(horizon_ms=5000)
        g2 = _make_group(horizon_ms=300000)
        assert corpus_config_key(g1) != corpus_config_key(g2)


# ---------------------------------------------------------------------------
# Test 2: DoesNotMergeDifferentConfigs
# ---------------------------------------------------------------------------

class TestDoesNotMergeDifferentConfigs:
    def test_different_lookbacks_separate(self, tmp_path):
        """Different lookback_ms produce separate aggregations."""
        groups = [
            _make_group(signal_type="notional_burst", lookback_ms=1000, horizon_ms=5000),
            _make_group(signal_type="notional_burst", lookback_ms=5000, horizon_ms=5000),
            _make_group(signal_type="signed_imbalance", lookback_ms=10000, horizon_ms=300000),
        ]
        report_dir = _write_report_dir(tmp_path, groups)
        aggregations = aggregate_corpus([report_dir])
        assert len(aggregations) == 3

    def test_different_horizons_separate(self, tmp_path):
        """Different horizon_ms produce separate aggregations."""
        groups = [
            _make_group(lookback_ms=1000, horizon_ms=5000),
            _make_group(lookback_ms=1000, horizon_ms=300000),
        ]
        report_dir = _write_report_dir(tmp_path, groups)
        aggregations = aggregate_corpus([report_dir])
        assert len(aggregations) == 2


# ---------------------------------------------------------------------------
# Test 3: DoesNotCherryPickBestResultAsPrimary
# ---------------------------------------------------------------------------

class TestDoesNotCherryPickBestResultAsPrimary:
    def test_sorted_by_num_captures_desc(self, tmp_path):
        """Aggregations sorted by num_captures desc, not by max mean_net_bps."""
        # Config A: appears in 3 captures, modest returns
        # Config B: appears in 1 capture, great returns
        groups_a = [
            _make_group(
                source_venue="binance_perp", target_venue="kraken",
                signal_type="notional_burst", lookback_ms=1000, horizon_ms=5000,
                mean_net_bps=5.0,
            ),
        ]
        groups_a2 = [
            _make_group(
                source_venue="binance_perp", target_venue="kraken",
                signal_type="notional_burst", lookback_ms=1000, horizon_ms=5000,
                mean_net_bps=3.0,
            ),
        ]
        groups_b = [
            _make_group(
                source_venue="binance_perp", target_venue="kraken",
                signal_type="big_winner", lookback_ms=2000, horizon_ms=10000,
                mean_net_bps=200.0,  # Much higher, but only 1 capture
            ),
        ]

        dir_a = _write_report_dir(tmp_path, groups_a, idx=0)
        dir_a2 = _write_report_dir(tmp_path, groups_a2, idx=1)
        dir_b = _write_report_dir(tmp_path, groups_b, idx=2)

        aggregations = aggregate_corpus([dir_a, dir_a2, dir_b])
        # Config A has 2 captures, Config B has 1 capture
        # Config A should come first despite lower returns
        assert len(aggregations) == 2
        assert aggregations[0].num_captures >= aggregations[1].num_captures
        # Config A (notional_burst) should be first
        assert aggregations[0].num_captures == 2
        assert aggregations[0].signal_type == "notional_burst"

    def test_mean_net_bps_not_primary_sort(self, tmp_path):
        """Verify that avg_mean_net_bps does NOT override num_captures for sort."""
        groups_low_return = [
            _make_group(
                source_venue="A", target_venue="B",
                signal_type="type1", lookback_ms=1000, horizon_ms=5000,
                mean_net_bps=2.0,
            ),
        ]
        groups_low_return2 = [
            _make_group(
                source_venue="A", target_venue="B",
                signal_type="type1", lookback_ms=1000, horizon_ms=5000,
                mean_net_bps=3.0,
            ),
        ]
        groups_high_return = [
            _make_group(
                source_venue="C", target_venue="D",
                signal_type="type2", lookback_ms=2000, horizon_ms=6000,
                mean_net_bps=100.0,
            ),
        ]
        dir1 = _write_report_dir(tmp_path, groups_low_return, idx=10)
        dir2 = _write_report_dir(tmp_path, groups_low_return2, idx=11)
        dir3 = _write_report_dir(tmp_path, groups_high_return, idx=12)

        aggregations = aggregate_corpus([dir1, dir2, dir3])
        # type1 has 2 captures, type2 has 1 capture
        # type1 (2 captures) should rank first even though returns are lower
        assert aggregations[0].signal_type == "type1"
        assert aggregations[0].num_captures == 2


# ---------------------------------------------------------------------------
# Test 4: HandlesEmptyReportDirs
# ---------------------------------------------------------------------------

class TestHandlesEmptyReportDirs:
    def test_empty_results_by_group(self, tmp_path):
        """Empty results_by_group produces empty aggregation list."""
        report_dir = _write_report_dir(tmp_path, [], idx=0)
        aggregations = aggregate_corpus([report_dir])
        assert aggregations == []

    def test_missing_summary_json(self, tmp_path):
        """Directory without summary.json is skipped."""
        empty_dir = tmp_path / "no_summary"
        empty_dir.mkdir()
        aggregations = aggregate_corpus([empty_dir])
        assert aggregations == []

    def test_no_report_dirs(self):
        """No report dirs produce empty aggregations."""
        aggregations = aggregate_corpus([])
        assert aggregations == []


# ---------------------------------------------------------------------------
# Test 5: AggregatesMultipleCaptures
# ---------------------------------------------------------------------------

class TestAggregatesMultipleCaptures:
    def test_two_reports_same_config(self, tmp_path):
        """Two report dirs with matching config produce correct num_captures=2."""
        groups_1 = [
            _make_group(
                source_venue="binance_perp",
                target_venue="kraken",
                signal_type="notional_burst",
                lookback_ms=1000,
                horizon_ms=5000,
                mean_net_bps=10.0,
            ),
        ]
        groups_2 = [
            _make_group(
                source_venue="binance_perp",
                target_venue="kraken",
                signal_type="notional_burst",
                lookback_ms=1000,
                horizon_ms=5000,
                mean_net_bps=15.0,
            ),
        ]

        dir1 = _write_report_dir(tmp_path, groups_1, idx=0)
        dir2 = _write_report_dir(tmp_path, groups_2, idx=1)

        aggregations = aggregate_corpus([dir1, dir2])
        assert len(aggregations) == 1
        agg = aggregations[0]
        assert agg.num_captures == 2
        assert agg.source_venue == "binance_perp"
        assert agg.target_venue == "kraken"
        assert agg.signal_type == "notional_burst"
        assert agg.lookback_ms == 1000
        assert agg.horizon_ms == 5000

    def test_aggregation_values_averaged(self, tmp_path):
        """Verify avg_mean_net_bps is computed correctly across captures."""
        groups_1 = [
            _make_group(
                source_venue="binance_perp",
                target_venue="kraken",
                signal_type="notional_burst",
                lookback_ms=1000,
                horizon_ms=5000,
                mean_net_bps=10.0,
                win_rate=0.5,
            ),
        ]
        groups_2 = [
            _make_group(
                source_venue="binance_perp",
                target_venue="kraken",
                signal_type="notional_burst",
                lookback_ms=1000,
                horizon_ms=5000,
                mean_net_bps=20.0,
                win_rate=0.7,
            ),
        ]

        dir1 = _write_report_dir(tmp_path, groups_1, idx=0)
        dir2 = _write_report_dir(tmp_path, groups_2, idx=1)

        aggregations = aggregate_corpus([dir1, dir2])
        assert len(aggregations) == 1
        agg = aggregations[0]
        assert agg.avg_mean_net_bps == pytest.approx(15.0, rel=1e-6)
        assert agg.avg_win_rate == pytest.approx(0.6, rel=1e-6)

    def test_format_corpus_report_not_empty(self, tmp_path):
        """format_corpus_report produces non-empty string."""
        groups = [
            _make_group(
                source_venue="binance_perp",
                target_venue="kraken",
                signal_type="notional_burst",
                lookback_ms=1000,
                horizon_ms=5000,
                mean_net_bps=10.0,
            ),
        ]
        report_dir = _write_report_dir(tmp_path, groups, idx=0)
        aggregations = aggregate_corpus([report_dir])
        report = format_corpus_report(aggregations)
        assert len(report) > 0
        assert "notional_burst" in report
        assert "binance_perp" in report