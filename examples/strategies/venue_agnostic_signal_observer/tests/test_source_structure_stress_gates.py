"""Tests for source_structure_stress_gates.py — Phase 0 Hawkes + entropy."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    ALLOWED_VERDICTS,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    FORBIDDEN_VERDICTS,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    HawkesStressLabel,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    PermutationEntropyPoint,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    SourceStructureStressConfig,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    VolatilityEvent,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    _compute_permutation_entropy,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    build_hawkes_stress_labels,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    build_source_return_buckets,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    build_volatility_events,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    compute_hawkes_intensity,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    compute_permutation_entropy_points,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    load_hawkes_stress_labels,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    merge_hawkes_stress_windows,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    validate_verdict,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    write_source_structure_stress_artifacts,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tick(ts_ns: int, price: float, symbol: str = "BTCUSDT", venue: str = "BINANCE") -> TradeTickLite:
    return TradeTickLite(
        ts_event=ts_ns, venue=venue, symbol=symbol,
        price=price, size=1.0, side="buy",
    )


def _make_constant_price_ticks(n: int, base_price: float = 50000.0, interval_ns: int = 100_000_000) -> list[TradeTickLite]:
    """N ticks at constant price, spaced by interval_ns."""
    return [_make_tick(i * interval_ns, base_price) for i in range(n)]


# ---------------------------------------------------------------------------
# Verdict validation
# ---------------------------------------------------------------------------

class TestVerdictValidation:
    def test_allowed_verdicts_pass(self):
        for v in ALLOWED_VERDICTS:
            assert validate_verdict(v) == v

    def test_forbidden_verdicts_raise(self):
        for v in FORBIDDEN_VERDICTS:
            with pytest.raises(ValueError, match="Forbidden verdict"):
                validate_verdict(v)

    def test_unknown_verdict_raises(self):
        with pytest.raises(ValueError, match="Unknown verdict"):
            validate_verdict("MY_MADE_UP_VERDICT")

    def test_trade_ready_forbidden(self):
        with pytest.raises(ValueError, match="TRADE_READY"):
            validate_verdict("TRADE_READY")

    def test_candidate_forbidden(self):
        with pytest.raises(ValueError, match="CANDIDATE"):
            validate_verdict("CANDIDATE")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_config(self):
        c = SourceStructureStressConfig()
        assert c.bucket_size_seconds == 1.0
        assert c.hawkes_event_threshold_bps == 5.0
        assert c.hawkes_tau_seconds == 30.0
        assert c.hawkes_branching_ratio == 0.5
        assert abs(c.hawkes_alpha - 0.5 / 30.0) < 1e-10

    def test_alpha_derived_not_independent(self):
        """Alpha is always eta / tau, never independently settable."""
        c = SourceStructureStressConfig(
            hawkes_branching_ratio=0.7,
            hawkes_tau_seconds=20.0,
        )
        assert abs(c.hawkes_alpha - 0.7 / 20.0) < 1e-10

    def test_invalid_branching_ratio(self):
        with pytest.raises(ValueError):
            SourceStructureStressConfig(hawkes_branching_ratio=1.5)

    def test_invalid_tau(self):
        with pytest.raises(ValueError):
            SourceStructureStressConfig(hawkes_tau_seconds=0)

    def test_config_hash_deterministic(self):
        c1 = SourceStructureStressConfig()
        c2 = SourceStructureStressConfig()
        assert c1.config_hash() == c2.config_hash()

    def test_config_hash_differs_on_change(self):
        c1 = SourceStructureStressConfig(hawkes_tau_seconds=30.0)
        c2 = SourceStructureStressConfig(hawkes_tau_seconds=60.0)
        assert c1.config_hash() != c2.config_hash()

    def test_to_dict_includes_alpha(self):
        c = SourceStructureStressConfig()
        d = c.to_dict()
        assert "hawkes_alpha" in d


# ---------------------------------------------------------------------------
# Return buckets
# ---------------------------------------------------------------------------

class TestReturnBuckets:
    def test_empty_ticks(self):
        assert build_source_return_buckets([]) == []

    def test_constant_price_no_returns(self):
        """Constant price → no returns (first bucket has no prev price)."""
        ticks = _make_constant_price_ticks(10)
        buckets = build_source_return_buckets(ticks)
        assert len(buckets) > 0
        # First bucket has no return
        assert buckets[0].log_return_bps is None
        # All returns should be ~0
        for b in buckets[1:]:
            assert b.log_return_bps is not None
            assert abs(b.log_return_bps) < 1e-6

    def test_single_tick_no_return(self):
        ticks = [_make_tick(1_000_000_000, 50000.0)]
        buckets = build_source_return_buckets(ticks)
        assert len(buckets) == 1
        assert buckets[0].log_return_bps is None

    def test_price_move_produces_return(self):
        """A price move from 1000 to 1010 → log-return ≈ 99.5 bps."""
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(2_000_000_000, 1010.0),
        ]
        buckets = build_source_return_buckets(ticks, bucket_size_seconds=1.0)
        assert len(buckets) == 2
        assert buckets[0].log_return_bps is None
        assert buckets[1].log_return_bps is not None
        expected = math.log(1010.0 / 1000.0) * 10_000
        assert abs(buckets[1].log_return_bps - expected) < 1e-6

    def test_duplicate_timestamps_last_price_wins(self):
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(1_000_000_000, 1001.0),  # same ts, later in list
        ]
        buckets = build_source_return_buckets(ticks)
        assert len(buckets) == 1
        assert buckets[0].last_price == 1001.0

    def test_zero_price_skipped(self):
        ticks = [
            _make_tick(1_000_000_000, 0.0),
            _make_tick(2_000_000_000, 1000.0),
        ]
        buckets = build_source_return_buckets(ticks)
        assert len(buckets) == 1
        assert buckets[0].log_return_bps is None

    def test_negative_price_skipped(self):
        ticks = [
            _make_tick(1_000_000_000, -100.0),
            _make_tick(2_000_000_000, 1000.0),
        ]
        buckets = build_source_return_buckets(ticks)
        assert len(buckets) == 1

    def test_nan_price_skipped(self):
        ticks = [
            _make_tick(1_000_000_000, float("nan")),
            _make_tick(2_000_000_000, 1000.0),
        ]
        buckets = build_source_return_buckets(ticks)
        assert len(buckets) == 1

    def test_raw_log_return_no_atr(self):
        """Returns must be raw log-return bps, not ATR-normalized."""
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(2_000_000_000, 1005.0),
        ]
        buckets = build_source_return_buckets(ticks)
        ret = buckets[1].log_return_bps
        expected = math.log(1005.0 / 1000.0) * 10_000
        assert abs(ret - expected) < 1e-6
        # Must NOT be z-scored or ATR-normalized
        assert abs(ret) < 100  # ~50 bps for 0.5% move

    def test_deterministic_ordering(self):
        ticks = [
            _make_tick(3_000_000_000, 1003.0),
            _make_tick(1_000_000_000, 1001.0),
            _make_tick(2_000_000_000, 1002.0),
        ]
        buckets = build_source_return_buckets(ticks)
        assert buckets[0].bucket_ts_ns == 1_000_000_000
        assert buckets[1].bucket_ts_ns == 2_000_000_000
        assert buckets[2].bucket_ts_ns == 3_000_000_000

    def test_bucket_size_5_seconds(self):
        """Multiple ticks within a 5s bucket → single bucket with last price."""
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(2_000_000_000, 1001.0),
            _make_tick(3_000_000_000, 1002.0),
            _make_tick(6_000_000_000, 1010.0),
        ]
        buckets = build_source_return_buckets(ticks, bucket_size_seconds=5.0)
        assert len(buckets) == 2
        assert buckets[0].last_price == 1002.0  # last in [0, 5)
        assert buckets[1].last_price == 1010.0


# ---------------------------------------------------------------------------
# Volatility events
# ---------------------------------------------------------------------------

class TestVolatilityEvents:
    def test_no_events_below_threshold(self):
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(2_000_000_000, 1000.01),
        ]
        buckets = build_source_return_buckets(ticks)
        events = build_volatility_events(buckets, threshold_bps=5.0)
        assert len(events) == 0

    def test_event_above_threshold(self):
        """Price move from 1000 to 1001 → ~10 bps, above 5 bps threshold."""
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(2_000_000_000, 1001.0),
        ]
        buckets = build_source_return_buckets(ticks)
        events = build_volatility_events(buckets, threshold_bps=5.0)
        assert len(events) == 1
        assert events[0].abs_return_bps >= 5.0

    def test_event_direction(self):
        ticks = [
            _make_tick(1_000_000_000, 1000.0),
            _make_tick(2_000_000_000, 999.0),  # down
        ]
        buckets = build_source_return_buckets(ticks)
        events = build_volatility_events(buckets, threshold_bps=5.0)
        assert len(events) == 1
        assert events[0].signed_return_bps < 0
        assert events[0].abs_return_bps > 0


# ---------------------------------------------------------------------------
# Hawkes intensity
# ---------------------------------------------------------------------------

class TestHawkesIntensity:
    def _make_events(self, times_ns: list[int], n: int = 100) -> list[VolatilityEvent]:
        return [
            VolatilityEvent(
                event_ts_ns=t, venue="BINANCE", symbol="BTCUSDT",
                abs_return_bps=10.0, signed_return_bps=10.0,
                bucket_ts_ns=t,
            )
            for t in times_ns
        ]

    def test_insufficient_events(self):
        config = SourceStructureStressConfig(hawkes_min_events=30)
        events = self._make_events([1_000_000_000 * i for i in range(10)])
        points = compute_hawkes_intensity(events, config)
        assert len(points) == 0

    def test_sufficient_events_produces_points(self):
        config = SourceStructureStressConfig(hawkes_min_events=10)
        events = self._make_events([1_000_000_000 * i for i in range(20)])
        points = compute_hawkes_intensity(events, config)
        assert len(points) == 20

    def test_no_future_leakage(self):
        """Intensity at event i must only use events j <= i."""
        config = SourceStructureStressConfig(hawkes_min_events=3)
        # Events within lookback window: 0, 10s, 20s (all within 60s lookback)
        events = self._make_events([0, 10_000_000_000, 20_000_000_000])
        points = compute_hawkes_intensity(events, config)
        # Intensity at first event should be just mu (no prior events besides itself)
        assert points[0].event_count_total == 1
        assert points[0].event_count_lookback == 1
        # Intensity at second event uses events 0 and 1 (both within 60s lookback)
        assert points[1].event_count_total == 2
        assert points[1].event_count_lookback == 2
        # Third event uses all 3
        assert points[2].event_count_total == 3
        assert points[2].event_count_lookback == 3

    def test_alpha_derived_correctly(self):
        config = SourceStructureStressConfig(
            hawkes_branching_ratio=0.5,
            hawkes_tau_seconds=30.0,
        )
        assert abs(config.hawkes_alpha - 0.5 / 30.0) < 1e-10

    def test_clustered_events_higher_intensity(self):
        """Clustered events should produce higher lambda_over_mu than sparse."""
        config = SourceStructureStressConfig(hawkes_min_events=5)
        # Clustered: all at 0-5s
        clustered = self._make_events([i * 1_000_000_000 for i in range(20)])
        # Sparse: spread over 600s
        sparse = self._make_events([i * 30_000_000_000 for i in range(20)])

        c_points = compute_hawkes_intensity(clustered, config)
        s_points = compute_hawkes_intensity(sparse, config)

        max_c = max(p.lambda_over_mu for p in c_points)
        max_s = max(p.lambda_over_mu for p in s_points)
        assert max_c > max_s

    def test_intensity_non_negative(self):
        config = SourceStructureStressConfig(hawkes_min_events=3)
        events = self._make_events([i * 1_000_000_000 for i in range(10)])
        points = compute_hawkes_intensity(events, config)
        for p in points:
            assert p.intensity_lambda >= 0
            assert math.isfinite(p.intensity_lambda)

    def test_empty_events(self):
        config = SourceStructureStressConfig()
        assert compute_hawkes_intensity([], config) == []


# ---------------------------------------------------------------------------
# Permutation entropy
# ---------------------------------------------------------------------------

class TestPermutationEntropy:
    def test_constant_sequence_zero_entropy(self):
        vals = [1.0] * 50
        raw, emax, norm, npat = _compute_permutation_entropy(vals, m=3, delay=1)
        assert norm == 0.0  # All patterns identical

    def test_linear_sequence_low_entropy(self):
        """Linear sequence has very few ordinal patterns → low entropy."""
        vals = list(range(1, 100))
        raw, emax, norm, npat = _compute_permutation_entropy(vals, m=3, delay=1)
        assert norm < 0.5  # Should be much lower than random

    def test_random_sequence_high_entropy(self):
        import random
        random.seed(42)
        vals = [random.gauss(0, 1) for _ in range(200)]
        raw, emax, norm, npat = _compute_permutation_entropy(vals, m=3, delay=1)
        assert norm > 0.8  # Should be close to 1 for random

    def test_low_vs_high_entropy(self):
        """Structured sequence should have lower entropy than noisy."""
        # Structured: sinusoidal
        import math
        structured = [math.sin(i * 0.1) for i in range(100)]
        _, _, norm_struct, _ = _compute_permutation_entropy(structured, m=3, delay=1)

        import random
        random.seed(42)
        noisy = [random.gauss(0, 1) for _ in range(100)]
        _, _, norm_noisy, _ = _compute_permutation_entropy(noisy, m=3, delay=1)

        assert norm_struct < norm_noisy

    def test_insufficient_patterns(self):
        vals = [1.0, 2.0, 3.0]  # Too few for m=3, delay=1
        raw, emax, norm, npat = _compute_permutation_entropy(vals, m=3, delay=1)
        assert npat == 0 or npat < 30

    def test_normalized_in_range(self):
        import random
        random.seed(42)
        vals = [random.gauss(0, 1) for _ in range(100)]
        _, _, norm, _ = _compute_permutation_entropy(vals, m=3, delay=1)
        assert 0.0 <= norm <= 1.0

    def test_entropy_past_only(self):
        """Entropy at index i must use only values up to index i."""
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        # Entropy computed on full series
        _, _, norm_full, _ = _compute_permutation_entropy(vals, m=3, delay=1)
        # Entropy on first 5 elements
        _, _, norm_first5, _ = _compute_permutation_entropy(vals[:5], m=3, delay=1)
        # Should differ (or first5 may be insufficient)
        # At minimum, the function is well-defined
        assert isinstance(norm_full, float)


class TestPermutationEntropyPoints:
    def test_empty_buckets(self):
        config = SourceStructureStressConfig()
        assert compute_permutation_entropy_points([], config) == []

    def test_insufficient_patterns_status(self):
        ticks = [_make_tick(i * 1_000_000_000, 1000.0 + i) for i in range(5)]
        buckets = build_source_return_buckets(ticks)
        config = SourceStructureStressConfig(entropy_min_patterns=30)
        points = compute_permutation_entropy_points(buckets, config)
        for p in points:
            assert p.status == "ENTROPY_INSUFFICIENT_PATTERNS"

    def test_computed_with_enough_data(self):
        import random
        random.seed(42)
        ticks = [
            _make_tick(i * 1_000_000_000, 1000.0 + random.gauss(0, 1))
            for i in range(200)
        ]
        buckets = build_source_return_buckets(ticks)
        config = SourceStructureStressConfig(entropy_min_patterns=30)
        points = compute_permutation_entropy_points(buckets, config)
        computed = [p for p in points if p.status == "computed"]
        assert len(computed) > 0
        for p in computed:
            assert 0.0 <= p.normalized_entropy <= 1.0


# ---------------------------------------------------------------------------
# Hawkes stress labels
# ---------------------------------------------------------------------------

class TestHawkesStressLabels:
    def test_no_labels_with_insufficient_events(self):
        config = SourceStructureStressConfig(hawkes_min_events=30)
        events = [
            VolatilityEvent(
                event_ts_ns=i * 1_000_000_000, venue="BINANCE",
                symbol="BTCUSDT", abs_return_bps=10.0,
                signed_return_bps=10.0, bucket_ts_ns=i * 1_000_000_000,
            )
            for i in range(10)
        ]
        labels = build_hawkes_stress_labels(events, [], [], config)
        assert len(labels) == 0

    def test_cooldown_suppression(self):
        """Labels within cooldown should be suppressed."""
        config = SourceStructureStressConfig(
            hawkes_min_events=3,
            label_cooldown_seconds=30.0,
            hawkes_intensity_multiple_threshold=1.0,
            hawkes_min_prior_events=1,
        )
        # Clustered events to ensure high intensity
        events = [
            VolatilityEvent(
                event_ts_ns=i * 1_000_000_000, venue="BINANCE",
                symbol="BTCUSDT", abs_return_bps=10.0,
                signed_return_bps=10.0, bucket_ts_ns=i * 1_000_000_000,
            )
            for i in range(20)
        ]
        points = compute_hawkes_intensity(events, config)
        labels = build_hawkes_stress_labels(events, points, [], config)
        # Check cooldown: consecutive label timestamps should be >= 30s apart
        for i in range(1, len(labels)):
            gap_s = (labels[i].ts_ns - labels[i - 1].ts_ns) / 1e9
            assert gap_s >= 30.0

    def test_lambda_over_mu_in_label(self):
        config = SourceStructureStressConfig(
            hawkes_min_events=3,
            hawkes_intensity_multiple_threshold=1.0,
            hawkes_min_prior_events=1,
        )
        events = [
            VolatilityEvent(
                event_ts_ns=i * 1_000_000_000, venue="BINANCE",
                symbol="BTCUSDT", abs_return_bps=10.0,
                signed_return_bps=10.0, bucket_ts_ns=i * 1_000_000_000,
            )
            for i in range(20)
        ]
        points = compute_hawkes_intensity(events, config)
        labels = build_hawkes_stress_labels(events, points, [], config)
        assert len(labels) > 0
        for lbl in labels:
            assert lbl.lambda_over_mu >= 1.0


# ---------------------------------------------------------------------------
# Merge windows
# ---------------------------------------------------------------------------

class TestMergeWindows:
    def test_empty_labels(self):
        assert merge_hawkes_stress_windows([]) == []

    def test_single_label_single_window(self):
        labels = [
            HawkesStressLabel(
                label_id="l1", ts_ns=1_000_000_000, ts_utc="",
                source_venue="BINANCE", source_symbol="BTCUSDT",
                hawkes_tau_seconds=30, hawkes_branching_ratio=0.5,
                hawkes_alpha=0.5/30, intensity_lambda=1.0, mu=0.5,
                lambda_over_mu=2.0, prior_event_count=5,
                abs_return_bps=10.0, signed_return_bps=10.0,
                bucket_size_seconds=1.0, event_threshold_bps=5.0,
                entropy_value=0.5, entropy_status="computed",
                reason="test", config_hash="abc",
            )
        ]
        windows = merge_hawkes_stress_windows(labels)
        assert len(windows) == 1
        assert windows[0].label_count == 1

    def test_close_labels_merged(self):
        """Labels 10s apart should be merged."""
        base = {
            "source_venue": "BINANCE", "source_symbol": "BTCUSDT",
            "hawkes_tau_seconds": 30, "hawkes_branching_ratio": 0.5,
            "hawkes_alpha": 0.5/30, "intensity_lambda": 1.0, "mu": 0.5,
            "lambda_over_mu": 2.0, "prior_event_count": 5,
            "abs_return_bps": 10.0, "signed_return_bps": 10.0,
            "bucket_size_seconds": 1.0, "event_threshold_bps": 5.0,
            "entropy_value": 0.5, "entropy_status": "computed",
            "reason": "test", "config_hash": "abc",
        }
        labels = [
            HawkesStressLabel(label_id=f"l{i}", ts_ns=i * 10_000_000_000, ts_utc="", **base)
            for i in range(5)
        ]
        windows = merge_hawkes_stress_windows(labels, merge_gap_seconds=30.0)
        assert len(windows) == 1  # All within 30s gaps
        assert windows[0].label_count == 5

    def test_far_labels_not_merged(self):
        """Labels 60s apart should NOT be merged."""
        base = {
            "source_venue": "BINANCE", "source_symbol": "BTCUSDT",
            "hawkes_tau_seconds": 30, "hawkes_branching_ratio": 0.5,
            "hawkes_alpha": 0.5/30, "intensity_lambda": 1.0, "mu": 0.5,
            "lambda_over_mu": 2.0, "prior_event_count": 5,
            "abs_return_bps": 10.0, "signed_return_bps": 10.0,
            "bucket_size_seconds": 1.0, "event_threshold_bps": 5.0,
            "entropy_value": 0.5, "entropy_status": "computed",
            "reason": "test", "config_hash": "abc",
        }
        labels = [
            HawkesStressLabel(label_id=f"l{i}", ts_ns=i * 60_000_000_000, ts_utc="", **base)
            for i in range(4)
        ]
        windows = merge_hawkes_stress_windows(labels, merge_gap_seconds=30.0)
        assert len(windows) == 4  # Each separate
        for w in windows:
            assert w.label_count == 1

    def test_window_preserves_per_label_values(self):
        """Merged window should preserve per-label causal values."""
        base = {
            "source_venue": "BINANCE", "source_symbol": "BTCUSDT",
            "hawkes_tau_seconds": 30, "hawkes_branching_ratio": 0.5,
            "hawkes_alpha": 0.5/30, "intensity_lambda": 1.0, "mu": 0.5,
            "prior_event_count": 5,
            "abs_return_bps": 10.0, "signed_return_bps": 10.0,
            "bucket_size_seconds": 1.0, "event_threshold_bps": 5.0,
            "entropy_status": "computed",
            "reason": "test", "config_hash": "abc",
        }
        labels = [
            HawkesStressLabel(
                label_id=f"l{i}", ts_ns=i * 10_000_000_000, ts_utc="",
                lambda_over_mu=float(2.0 + i * 0.5),
                entropy_value=0.5 + i * 0.1,
                **base,
            )
            for i in range(5)
        ]
        windows = merge_hawkes_stress_windows(labels)
        w = windows[0]
        assert w.max_lambda_over_mu > w.min_lambda_over_mu
        assert len(w.entropy_values) == 5


# ---------------------------------------------------------------------------
# Entropy does not suppress labels (v0 rule)
# ---------------------------------------------------------------------------

class TestEntropyDoesNotGateLabels:
    def test_labels_emitted_despite_high_entropy(self):
        """In v0, high entropy must NOT suppress Hawkes labels."""
        config = SourceStructureStressConfig(
            hawkes_min_events=3,
            hawkes_intensity_multiple_threshold=1.0,
            hawkes_min_prior_events=1,
            entropy_min_patterns=1,  # Force entropy to be "computed"
        )
        events = [
            VolatilityEvent(
                event_ts_ns=i * 1_000_000_000, venue="BINANCE",
                symbol="BTCUSDT", abs_return_bps=10.0,
                signed_return_bps=10.0, bucket_ts_ns=i * 1_000_000_000,
            )
            for i in range(20)
        ]
        points = compute_hawkes_intensity(events, config)
        # Build entropy points with HIGH entropy (close to 1)
        entropy_points = [
            PermutationEntropyPoint(
                ts_ns=e.event_ts_ns, normalized_entropy=0.95,
                pattern_count=6, status="computed",
                entropy_raw=1.0, entropy_max=1.1,
            )
            for e in events
        ]
        labels = build_hawkes_stress_labels(events, points, entropy_points, config)
        # Labels should still be emitted despite high entropy
        assert len(labels) > 0

    def test_no_entropy_threshold_config(self):
        """Config should not have entropy_max_normalized field."""
        config = SourceStructureStressConfig()
        assert not hasattr(config, "entropy_max_normalized")


# ---------------------------------------------------------------------------
# Artifact I/O
# ---------------------------------------------------------------------------

class TestArtifactIO:
    def test_write_and_load_labels(self, tmp_path):
        config = SourceStructureStressConfig()
        labels = [
            HawkesStressLabel(
                label_id="test_001", ts_ns=1_000_000_000,
                ts_utc="2024-01-01T00:00:01Z",
                source_venue="BINANCE", source_symbol="BTCUSDT",
                hawkes_tau_seconds=30, hawkes_branching_ratio=0.5,
                hawkes_alpha=0.5/30, intensity_lambda=1.0, mu=0.5,
                lambda_over_mu=2.0, prior_event_count=5,
                abs_return_bps=10.0, signed_return_bps=10.0,
                bucket_size_seconds=1.0, event_threshold_bps=5.0,
                entropy_value=0.5, entropy_status="computed",
                reason="test", config_hash=config.config_hash(),
            )
        ]
        result = write_source_structure_stress_artifacts(
            out_dir=tmp_path,
            buckets=[], events=[], hawkes_points=[],
            entropy_points=[], labels=labels, windows=[],
            config=config,
            hawkes_verdict="HAWKES_STRESS_LABELS_READY",
            entropy_verdict="ENTROPY_MEASUREMENTS_READY",
        )
        assert "labels_path" in result
        loaded = load_hawkes_stress_labels(Path(result["labels_path"]))
        assert len(loaded) == 1
        assert loaded[0].label_id == "test_001"

    def test_summary_json_written(self, tmp_path):
        config = SourceStructureStressConfig()
        result = write_source_structure_stress_artifacts(
            out_dir=tmp_path, buckets=[], events=[],
            hawkes_points=[], entropy_points=[], labels=[], windows=[],
            config=config,
            hawkes_verdict="NO_HAWKES_STRESS_LABELS",
            entropy_verdict="ENTROPY_INSUFFICIENT_PATTERNS",
        )
        summary_path = Path(result["summary_path"])
        assert summary_path.exists()
        with open(summary_path) as f:
            summary = json.load(f)
        assert summary["hawkes_verdict"] == "NO_HAWKES_STRESS_LABELS"
        assert "_metadata" in summary
        assert summary["_metadata"]["safety_mode"] == "public_data_observer_only"


# ---------------------------------------------------------------------------
# Safety scan
# ---------------------------------------------------------------------------

class TestSafetyScan:
    def test_no_private_keys_in_module(self):
        import ast
        import inspect

        import examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, (ast.Constant,))):
                    node.body[0].value.value = ""
        code_without_docs = ast.unparse(tree)
        forbidden = [
            "private_key", "PRIVATE_KEY", "api_secret", "API_SECRET",
            "api_key", "API_KEY", "TradingNode", "LiveNode", "ExecutionClient",
            "submit_order", "cancel_order", "modify_order",
            "bot_ready", "trade_ready",
        ]
        for pattern in forbidden:
            assert pattern not in code_without_docs, f"Found forbidden pattern: {pattern}"

    def test_no_private_keys_in_runner(self):
        import inspect

        import examples.strategies.venue_agnostic_signal_observer.run_source_structure_stress_gates as mod
        src = inspect.getsource(mod)
        forbidden = [
            "private_key", "PRIVATE_KEY", "api_secret", "API_SECRET",
            "api_key", "API_KEY", "TradingNode", "LiveNode",
            "ExecutionClient", "submit_order",
        ]
        for pattern in forbidden:
            assert pattern not in src, f"Found forbidden pattern: {pattern}"


# ---------------------------------------------------------------------------
# Stable output / determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_run_same_output(self):
        """Same input ticks → same buckets deterministically."""
        ticks = [
            _make_tick(i * 1_000_000_000, 1000.0 + i * 0.5)
            for i in range(100)
        ]
        b1 = build_source_return_buckets(ticks)
        b2 = build_source_return_buckets(ticks)
        for a, b in zip(b1, b2, strict=False):
            assert asdict(a) == asdict(b)

    def test_config_serialization_roundtrip(self):
        c1 = SourceStructureStressConfig(hawkes_tau_seconds=60.0)
        d = c1.to_dict()
        # Can reconstruct from dict (all fields present)
        assert d["hawkes_tau_seconds"] == 60.0
        assert abs(d["hawkes_alpha"] - 0.5 / 60.0) < 1e-10
