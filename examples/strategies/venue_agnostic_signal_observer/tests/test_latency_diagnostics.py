"""
Tests for latency_diagnostics module.

Tests:
1. DetectsZeroOverlapStreams — no temporal overlap → overlap_seconds ≈ 0
2. ComputesInterTickGapStats — known timestamps → correct median/p95 gap
3. MarksSparseStreams — median gap > 10s → sparse_warning=True
4. HandlesMissingVenuesCleanly — empty dir → empty stream stats dict, no crash
5. SubSecondHorizonRecommendation — fast/mixed/slow streams → safe/cautious/unsafe
"""
from __future__ import annotations

import pytest

from venue_agnostic_signal_observer.latency_diagnostics import StreamStats
from venue_agnostic_signal_observer.latency_diagnostics import compute_overlap_stats
from venue_agnostic_signal_observer.latency_diagnostics import load_stream_stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stream_stats(
    *,
    venue: str = "binance_perp",
    symbol: str = "BTC/USD",
    tick_count: int = 1000,
    first_ts_ns: int = 1_700_000_000_000_000_000,
    last_ts_ns: int = 1_700_003_600_000_000_000,  # 1 hour later
    median_gap_ns: float = 500_000_000.0,  # 0.5s
    p95_gap_ns: float = 2_000_000_000.0,  # 2s
) -> StreamStats:
    """Build a StreamStats instance."""
    return StreamStats(
        venue=venue,
        symbol=symbol,
        tick_count=tick_count,
        first_ts_ns=first_ts_ns,
        last_ts_ns=last_ts_ns,
        median_inter_tick_gap_ns=median_gap_ns,
        p95_inter_tick_gap_ns=p95_gap_ns,
    )


# ---------------------------------------------------------------------------
# Test 1: DetectsZeroOverlapStreams
# ---------------------------------------------------------------------------

class TestDetectsZeroOverlapStreams:
    def test_no_temporal_overlap(self):
        """Two StreamStats with no temporal overlap produce overlap_seconds ≈ 0."""
        stream_a = _make_stream_stats(
            venue="venue_a",
            first_ts_ns=1_000_000_000,
            last_ts_ns=2_000_000_000,
        )
        stream_b = _make_stream_stats(
            venue="venue_b",
            first_ts_ns=3_000_000_000,  # Starts after stream_a ends
            last_ts_ns=4_000_000_000,
        )
        result = compute_overlap_stats(stream_a, stream_b)
        assert result.overlap_seconds < 0.01  # Overlap should be ~ 0

    def test_narrow_overlap(self):
        """Streams that barely overlap have small overlap_seconds."""
        stream_a = _make_stream_stats(
            first_ts_ns=1_000_000_000,
            last_ts_ns=2_000_000_000,
        )
        stream_b = _make_stream_stats(
            first_ts_ns=1_999_000_000,  # Overlaps only last 1s of a
            last_ts_ns=3_000_000_000,
        )
        result = compute_overlap_stats(stream_a, stream_b)
        # Overlap = min(2B, 3B) - max(1B, 1.999B) = 2B - 1.999B = 0.001B = 1ms
        # In ns: 1_000_000 ns = 0.001 s
        overlap = result.overlap_seconds
        assert overlap >= 0
        assert overlap < 0.01


# ---------------------------------------------------------------------------
# Test 2: ComputesInterTickGapStats
# ---------------------------------------------------------------------------

class TestComputesInterTickGapStats:
    def test_known_timestamps(self):
        """Create a stream with known inter-tick gaps and verify stats."""
        # 5 ticks at exactly 1-second intervals starting at t=0
        # Gaps: 1s, 1s, 1s, 1s → all 1s = 1_000_000_000 ns
        stream = _make_stream_stats(
            tick_count=5,
            first_ts_ns=0,
            last_ts_ns=4_000_000_000,
            median_gap_ns=1_000_000_000.0,
            p95_gap_ns=1_000_000_000.0,
        )
        assert stream.tick_count == 5
        assert stream.duration_s == pytest.approx(4.0, rel=1e-6)
        assert stream.median_gap_s == pytest.approx(1.0, rel=1e-6)
        assert stream.p95_gap_s == pytest.approx(1.0, rel=1e-6)

    def test_stream_stats_properties(self):
        """Verify computed properties of StreamStats."""
        stream = _make_stream_stats(
            first_ts_ns=0,
            last_ts_ns=600_000_000_000,  # 600s = 10 minutes
            median_gap_ns=500_000_000,  # 0.5s
            p95_gap_ns=5_000_000_000,  # 5s
        )
        assert stream.duration_s == pytest.approx(600.0, rel=1e-6)
        assert stream.median_gap_s == pytest.approx(0.5, rel=1e-3)
        assert stream.p95_gap_s == pytest.approx(5.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Test 3: MarksSparseStreams
# ---------------------------------------------------------------------------

class TestMarksSparseStreams:
    def test_sparse_stream_flagged(self):
        """A stream with median gap > 10s gets sparse_warning=True."""
        stream_a = _make_stream_stats(
            venue="slow_venue",
            median_gap_ns=15_000_000_000.0,  # 15s > 10s threshold
            p95_gap_ns=30_000_000_000.0,
        )
        stream_b = _make_stream_stats(
            venue="fast_venue",
            first_ts_ns=stream_a.first_ts_ns,
            last_ts_ns=stream_a.last_ts_ns,
            median_gap_ns=500_000_000.0,
            p95_gap_ns=2_000_000_000.0,
        )
        result = compute_overlap_stats(stream_a, stream_b)
        assert result.sparse_warning_a is True
        assert result.sparse_warning_b is False

    def test_fast_stream_not_flagged(self):
        """Two fast streams both have sparse_warning=False."""
        stream_a = _make_stream_stats(venue="fast_a", median_gap_ns=500_000_000.0)
        stream_b = _make_stream_stats(venue="fast_b", median_gap_ns=800_000_000.0)
        result = compute_overlap_stats(stream_a, stream_b)
        assert result.sparse_warning_a is False
        assert result.sparse_warning_b is False


# ---------------------------------------------------------------------------
# Test 4: HandlesMissingVenuesCleanly
# ---------------------------------------------------------------------------

class TestHandlesMissingVenuesCleanly:
    def test_empty_dir(self, tmp_path):
        """Empty capture dir produces empty stream stats dict, no crash."""
        empty_dir = tmp_path / "empty_capture"
        empty_dir.mkdir()
        result = load_stream_stats(empty_dir)
        assert result == {}

    def test_nonexistent_dir(self, tmp_path):
        """Non-existent directory returns empty dict."""
        nonexistent = tmp_path / "does_not_exist"
        result = load_stream_stats(nonexistent)
        assert result == {}

    def test_dir_without_trades_files(self, tmp_path):
        """Directory with no trades_*.jsonl files returns empty dict."""
        capture_dir = tmp_path / "capture"
        capture_dir.mkdir()
        # Write a non-trades file
        (capture_dir / "other_data.txt").write_text("some data")
        result = load_stream_stats(capture_dir)
        assert result == {}


# ---------------------------------------------------------------------------
# Test 5: SubSecondHorizonRecommendation
# ---------------------------------------------------------------------------

class TestSubSecondHorizonRecommendation:
    def test_fast_streams_high_confidence(self):
        """Two fast streams (median < 0.5s) with long overlap → 'high' confidence."""
        stream_a = _make_stream_stats(
            venue="binance_perp",
            symbol="BTC/USD",
            first_ts_ns=1_700_000_000_000_000_000,
            last_ts_ns=1_700_036_000_000_000_000,  # 1 hour = 3600s
            median_gap_ns=200_000_000.0,  # 0.2s
            p95_gap_ns=1_000_000_000.0,
        )
        stream_b = _make_stream_stats(
            venue="kraken",
            symbol="BTC/USD",
            first_ts_ns=1_700_000_000_000_000_000,
            last_ts_ns=1_700_036_000_000_000_000,
            median_gap_ns=300_000_000.0,  # 0.3s
            p95_gap_ns=1_500_000_000.0,
        )
        result = compute_overlap_stats(stream_a, stream_b)
        assert result.sub_second_confidence == "high"

    def test_mixed_streams_medium_confidence(self):
        """One fast (median < 2s) and one slow-ish stream with good overlap → 'medium'."""
        stream_a = _make_stream_stats(
            venue="venue_a",
            first_ts_ns=1_700_000_000_000_000_000,
            last_ts_ns=1_700_006_000_000_000_000,  # 6000s = 100 min > 60s
            median_gap_ns=1_000_000_000.0,  # 1.0s < 2.0s
            p95_gap_ns=5_000_000_000.0,
        )
        stream_b = _make_stream_stats(
            venue="venue_b",
            first_ts_ns=1_700_000_000_000_000_000,
            last_ts_ns=1_700_006_000_000_000_000,
            median_gap_ns=1_500_000_000.0,  # 1.5s < 2.0s
            p95_gap_ns=6_000_000_000.0,
        )
        result = compute_overlap_stats(stream_a, stream_b)
        # Both are < 2s and overlap > 60s, but not both < 0.5s
        assert result.sub_second_confidence in ("medium", "high")

    def test_slow_streams_low_or_unsafe(self):
        """Two slow streams (median gap > 10s) → low or unsafe confidence."""
        stream_a = _make_stream_stats(
            venue="slow_a",
            first_ts_ns=1_700_000_000_000_000_000,
            last_ts_ns=1_700_060_000_000_000_000,  # 60ks
            median_gap_ns=15_000_000_000.0,  # 15s > threshold
            p95_gap_ns=60_000_000_000.0,
        )
        stream_b = _make_stream_stats(
            venue="slow_b",
            first_ts_ns=1_700_000_000_000_000_000,
            last_ts_ns=1_700_060_000_000_000_000,
            median_gap_ns=12_000_000_000.0,  # 12s > threshold
            p95_gap_ns=50_000_000_000.0,
        )
        result = compute_overlap_stats(stream_a, stream_b)
        assert result.sub_second_confidence in ("low", "unsafe")
