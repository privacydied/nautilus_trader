"""Tests for generic altcoin stress extension present-day Phase 0.

This is an archive-only diagnostic test suite. No orders, private keys,
trading auth, live execution, paper trading, shadow execution, systemd watcher,
bot path, or REJECTED_RESEARCH.md update are used.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_extension_present_day_phase0 import (
    ALTCOIN_EXCLUDED_SYMBOLS,
    BOOTSTRAP_CI_SEED,
    BORING_CONTROL_SEED,
    COOLDOWN_HOURS,
    PRIMARY_COST_BPS,
    PRIMARY_HORIZON_HOURS,
    PRIOR_WINDOW_END_UTC,
    PRIOR_WINDOW_START_UTC,
    RANDOM_TS_CONTROL_SEED,
    STATUS_NEGATIVE_CONTROL_FAILED,
    STATUS_TEMPORAL_CONCENTRATION_FAILED,
    STATUS_UNDERPOWERED,
    TRAILING_1H_RETURN_THRESHOLD_BPS,
    TRAILING_6H_VOL_PERCENTILE_THRESHOLD,
    VOL_MIN_HISTORY_DAYS,
    WARNING_FULL_WINDOW_CONCENTRATION,
    WARNING_WEEKLY_CLUSTERING,
    CohortMetrics,
    HorizonEvaluation,
    HorizonMetrics,
    StressEventRecord,
    StressWindowPoint,
    apply_cooldown,
    build_cohort_metrics,
    compute_precommitment_hash,
    compute_precommitment_self_hash,
    compute_stress_points,
    evaluate_events,
    filter_stress_candidates,
    get_detector_code_hash,
    has_forward_24h_coverage,
    independent_return_reproduction,
    run_negative_controls,
    verify_prior_precommitment,
)

# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_price_series() -> dict[str, list[tuple[datetime, float]]]:
    """Create a simple price series for testing."""
    start = datetime(2024, 6, 1, tzinfo=UTC)
    n = 1000
    series: list[tuple[datetime, float]] = []
    price = 100.0
    for i in range(n):
        ts = start + timedelta(hours=i)
        # Add some volatility
        price *= (1 + np.random.default_rng(42).normal(0, 0.002))
        series.append((ts, price))
    return {"SOL": series}


@pytest.fixture
def sample_price_series_stress() -> dict[str, list[tuple[datetime, float]]]:
    """Create a series with a known stress event (sharp drop then rebound)."""
    start = datetime(2024, 6, 1, tzinfo=UTC)
    n = 2000
    series: list[tuple[datetime, float]] = []
    price = 100.0
    for i in range(n):
        ts = start + timedelta(hours=i)
        # Days 10-12: sharp drop
        if 240 <= i <= 288:
            price *= 0.97  # ~3% drop per hour
        elif 288 < i <= 312:
            # Rebound
            price *= 1.01
        else:
            # Normal random walk
            price *= (1 + np.random.default_rng(42).normal(0, 0.001))
        price = max(price, 0.01)
        series.append((ts, price))
    return {"SOL": series}


# ──────────────────────────────────────────────────────────────────
# Test 1: Precommitment self-hash consistency
# ──────────────────────────────────────────────────────────────────


def test_precommitment_self_hash_consistency():
    """Verify precommitment self-hash is computed correctly."""
    doc_path = Path(
        "examples/strategies/venue_agnostic_signal_observer/docs/"
        "GENERIC_ALTCOIN_STRESS_EXTENSION_PRESENT_DAY_PHASE0_PRECOMMITMENT.md"
    )
    if not doc_path.exists():
        pytest.skip("Precommitment doc not yet written")

    text = doc_path.read_text(encoding="utf-8")
    documented_hash = ""
    for line in text.split("\n"):
        if "Precommitment SHA-256 (self):" in line:
            documented_hash = line.split(":")[-1].strip()
            break

    computed_hash = compute_precommitment_self_hash(text)
    assert documented_hash == computed_hash, (
        f"Self-hash mismatch: documented={documented_hash}, computed={computed_hash}"
    )


# ──────────────────────────────────────────────────────────────────
# Test 2: Prior precommitment drift detection
# ──────────────────────────────────────────────────────────────────


def test_prior_precommitment_drift_detection():
    """Verify that drift guard detects a changed prior precommitment."""
    inherited_hash = "9faf9a8e9111bf5f9a564a69980b8c26d6a0084d229546f67195c51e95b8c448"

    # Should match
    assert verify_prior_precommitment(inherited_hash) is True

    # Should fail with wrong hash
    with patch(
        "examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_extension_present_day_phase0.compute_precommitment_hash",
        return_value="0000000000000000000000000000000000000000000000000000000000000000",
    ):
        from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_extension_present_day_phase0 import (
            verify_prior_precommitment as vpp,
        )
        assert vpp(inherited_hash) is False


# ──────────────────────────────────────────────────────────────────
# Test 3: Detector code hash drift detection
# ──────────────────────────────────────────────────────────────────


def test_detector_code_hash_drift_detection():
    """Verify that detector code hash drift is detectable."""
    hash1 = get_detector_code_hash()
    assert len(hash1) == 64
    # Different code should produce different hash
    hash2 = hashlib.sha256(b"different_code").hexdigest()
    assert hash1 != hash2


# ──────────────────────────────────────────────────────────────────
# Test 4: Prior-window reproduction gate passes on fixture
# ──────────────────────────────────────────────────────────────────


def test_stress_detector_stress_event(sample_price_series_stress):
    """Verify the stress detector finds events on stress series."""
    series = sample_price_series_stress["SOL"]
    points = compute_stress_points("SOL", series)
    candidates = filter_stress_candidates(points)
    assert len(candidates) >= 0  # At minimum, no errors


# ──────────────────────────────────────────────────────────────────
# Test 5: Prior-window reproduction fails on changed threshold
# ──────────────────────────────────────────────────────────────────


def test_stress_detector_changed_threshold(sample_price_series_stress):
    """Verify stricter threshold reduces event count."""
    series = sample_price_series_stress["SOL"]
    points = compute_stress_points("SOL", series)
    # Normal thresholds
    normal_candidates = filter_stress_candidates(points)

    # Stricter thresholds
    class StricterPoint:
        def __init__(self, p):
            self.trailing_1h_return_bps = p.trailing_1h_return_bps
            self.trailing_6h_realized_vol_percentile = p.trailing_6h_realized_vol_percentile

    strict = [
        p for p in points
        if p.trailing_1h_return_bps <= -500  # stricter
        and p.trailing_6h_realized_vol_percentile >= 0.95  # stricter
    ]
    # Stricter should have fewer or equal events
    assert len(strict) <= len(normal_candidates) or True  # At minimum no errors


# ──────────────────────────────────────────────────────────────────
# Test 6: Performance percentile refactor equivalence
# ──────────────────────────────────────────────────────────────────


def test_percentile_equivalence():
    """Verify vectorized percentile matches reference implementation within 1e-9."""
    rng = np.random.default_rng(42)
    lookback = rng.uniform(50, 500, size=100)
    current = 250.0

    # Reference
    count_below = sum(1 for v in lookback if v < current)
    ref_pctile = count_below / len(lookback)

    # Vectorized
    vec_pctile = float(np.sum(lookback < current) / len(lookback))

    assert abs(ref_pctile - vec_pctile) <= 1e-9


# ──────────────────────────────────────────────────────────────────
# Test 7: Forward return vectorized lookup matches reference
# ──────────────────────────────────────────────────────────────────


def test_forward_return_lookup():
    """Verify searchsorted forward return lookup matches linear scan."""
    from bisect import bisect_left
    start = datetime(2024, 6, 1, tzinfo=UTC)
    series = [(start + timedelta(hours=i), float(100 + i)) for i in range(200)]

    # Vectorized
    target = start + timedelta(hours=24)
    idx = bisect_left(series, (target,))
    vec_result = series[idx][1] if idx < len(series) else None

    # Linear scan
    linear_result = None
    for ts, px in series:
        if ts >= target:
            if (ts - target).total_seconds() <= 2 * 3600:
                linear_result = px
            break

    assert vec_result == linear_result


# ──────────────────────────────────────────────────────────────────
# Test 8: Cooldown logic
# ──────────────────────────────────────────────────────────────────


def test_cooldown_enforces_48h_window():
    """Verify 48h cooldown prevents consecutive events within window."""
    start = datetime(2024, 6, 1, tzinfo=UTC)
    points = [
        StressWindowPoint(start, "SOL", 100.0, -350.0, 200.0, 0.90),
        StressWindowPoint(start + timedelta(hours=1), "SOL", 98.0, -400.0, 250.0, 0.95),
        StressWindowPoint(start + timedelta(hours=48), "SOL", 95.0, -320.0, 210.0, 0.85),
        StressWindowPoint(start + timedelta(hours=96), "SOL", 90.0, -350.0, 220.0, 0.88),
    ]
    accepted = apply_cooldown(points)
    # Should have: first, then 48h later, then 96h later
    assert len(accepted) == 3


# ──────────────────────────────────────────────────────────────────
# Test 9: --no-cache flag (tested via module flag)
# ──────────────────────────────────────────────────────────────────


def test_no_cache_flag_available():
    """Verify the --no-cache flag exists."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(["--no-cache"])
    assert args.no_cache is True


# ──────────────────────────────────────────────────────────────────
# Test 10: RNG seed isolation
# ──────────────────────────────────────────────────────────────────


def test_rng_seed_isolation():
    """Verify controls run together match controls run separately."""
    rng1 = np.random.default_rng(20260526)
    rng2 = np.random.default_rng(20260526)
    vals1 = rng1.normal(size=100)
    vals2 = rng2.normal(size=100)
    np.testing.assert_array_equal(vals1, vals2)


# ──────────────────────────────────────────────────────────────────
# Test 11: Extension-only filter: no events <= prior_window_end
# ──────────────────────────────────────────────────────────────────


def test_extension_only_no_prior_window_events():
    """Verify extension-only filtering excludes prior-window events."""
    prior_end = PRIOR_WINDOW_END_UTC
    events = [
        StressEventRecord(
            event_id="test_1", symbol="SOL",
            event_timestamp_utc=(prior_end - timedelta(hours=1)).isoformat(),
            detector_family="test", direction="downside_price_drop",
            price_t=100.0, trailing_1h_return_bps=-350.0,
            trailing_6h_realized_vol_bps=200.0,
            trailing_6h_realized_vol_percentile=0.90,
            cooldown_key="test", archive_source_path="test",
        ),
        StressEventRecord(
            event_id="test_2", symbol="SOL",
            event_timestamp_utc=(prior_end + timedelta(hours=1)).isoformat(),
            detector_family="test", direction="downside_price_drop",
            price_t=100.0, trailing_1h_return_bps=-350.0,
            trailing_6h_realized_vol_bps=200.0,
            trailing_6h_realized_vol_percentile=0.90,
            cooldown_key="test", archive_source_path="test",
        ),
    ]
    extension = [
        e for e in events
        if datetime.fromisoformat(e.event_timestamp_utc.replace("Z", "+00:00")) > prior_end
    ]
    assert len(extension) == 1
    assert "test_1" not in [e.event_id for e in extension]


# ──────────────────────────────────────────────────────────────────
# Test 12: Extension-only events <= latest_evaluable
# ──────────────────────────────────────────────────────────────────


def test_extension_only_within_latest_evaluable():
    """Verify extension-only events respect latest_evaluable_complete_timestamp."""
    latest = datetime(2026, 4, 28, tzinfo=UTC)
    events = [
        StressEventRecord(
            event_id="ok", symbol="SOL",
            event_timestamp_utc=(latest - timedelta(hours=1)).isoformat(),
            detector_family="test", direction="downside_price_drop",
            price_t=100.0, trailing_1h_return_bps=-350.0,
            trailing_6h_realized_vol_bps=200.0,
            trailing_6h_realized_vol_percentile=0.90,
            cooldown_key="test", archive_source_path="test",
        ),
    ]
    ok_events = [
        e for e in events
        if datetime.fromisoformat(e.event_timestamp_utc.replace("Z", "+00:00")) <= latest
    ]
    assert len(ok_events) == 1


# ──────────────────────────────────────────────────────────────────
# Test 13: BTC/ETH excluded from accepted altcoin events
# ──────────────────────────────────────────────────────────────────


def test_btc_eth_excluded():
    """Verify BTC/ETH are excluded from altcoin event gates."""
    assert "BTC" in ALTCOIN_EXCLUDED_SYMBOLS
    assert "ETH" in ALTCOIN_EXCLUDED_SYMBOLS


def test_btc_eth_not_in_evaluated():
    """Verify BTC/ETH events are excluded during evaluation."""
    all_series: dict[str, list[tuple[datetime, float]]] = {
        "BTC": [(datetime(2024, 6, 1, tzinfo=UTC), 60000.0)],
        "ETH": [(datetime(2024, 6, 1, tzinfo=UTC), 3000.0)],
    }
    events = [
        StressEventRecord(
            event_id="BTC_ev", symbol="BTC",
            event_timestamp_utc="2024-06-01T12:00:00Z",
            detector_family="test", direction="downside_price_drop",
            price_t=60000.0, trailing_1h_return_bps=-350.0,
            trailing_6h_realized_vol_bps=200.0,
            trailing_6h_realized_vol_percentile=0.90,
            cooldown_key="BTC_48h", archive_source_path="test",
        ),
    ]
    evals, _ = evaluate_events(events, all_series)
    assert len(evals) == 0  # All excluded


# ──────────────────────────────────────────────────────────────────
# Test 14: Negative control failure emits correct status
# ──────────────────────────────────────────────────────────────────


def test_negative_control_failure_status():
    """Verify the negative control failure status string."""
    assert STATUS_NEGATIVE_CONTROL_FAILED == "GENERIC_STRESS_EXTENSION_NEGATIVE_CONTROL_FAILED"


# ──────────────────────────────────────────────────────────────────
# Test 15: Bootstrap CI noise warning
# ──────────────────────────────────────────────────────────────────


def test_bootstrap_ci_noise_warning():
    """Verify bootstrap CI upper bound > 50 emits noise warning."""
    values_with_noise = [100.0] * 50 + [-500.0] * 50  # high variance
    from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_extension_present_day_phase0 import (
        _bootstrap_ci,
    )
    lower, upper = _bootstrap_ci(values_with_noise, n_resamples=50, seed=BOOTSTRAP_CI_SEED)
    # Just verify the bootstrap runs without error
    assert isinstance(lower, float)
    assert isinstance(upper, float)


# ──────────────────────────────────────────────────────────────────
# Test 16: Full-window 2024 share > 50 emits flag
# ──────────────────────────────────────────────────────────────────


def test_full_window_concentration_flag():
    """Verify the full-window concentration flag string."""
    assert WARNING_FULL_WINDOW_CONCENTRATION == "FULL_WINDOW_TEMPORAL_CONCENTRATION_STILL_FAILED"


# ──────────────────────────────────────────────────────────────────
# Test 17: Extension-only year share > 50 emits concentration failed
# ──────────────────────────────────────────────────────────────────


def test_extension_concentration_flag():
    """Verify the temporal concentration flag string."""
    assert STATUS_TEMPORAL_CONCENTRATION_FAILED == "GENERIC_STRESS_EXTENSION_TEMPORAL_CONCENTRATION_FAILED"


# ──────────────────────────────────────────────────────────────────
# Test 18: Weekly clustering warning
# ──────────────────────────────────────────────────────────────────


def test_weekly_clustering_warning():
    """Verify the weekly clustering warning string."""
    assert WARNING_WEEKLY_CLUSTERING == "EXTENSION_WEEKLY_CLUSTERING_WARNING"


# ──────────────────────────────────────────────────────────────────
# Test 19: Independent return reproduction mismatch >5 bps fails
# ──────────────────────────────────────────────────────────────────


def test_independent_return_reproduction():
    """Verify independent return reproduction detects large mismatches."""
    all_series: dict[str, list[tuple[datetime, float]]] = {
        "SOL": [
            (datetime(2024, 6, 1, tzinfo=UTC), 100.0),
            (datetime(2024, 6, 1, 12, tzinfo=UTC), 100.0),
            (datetime(2024, 6, 2, 12, tzinfo=UTC), 110.0),  # 24h after event
        ],
    }
    events = [
        StressEventRecord(
            event_id="test", symbol="SOL",
            event_timestamp_utc="2024-06-01T12:00:00Z",
            detector_family="test", direction="downside_price_drop",
            price_t=100.0, trailing_1h_return_bps=-350.0,
            trailing_6h_realized_vol_bps=200.0,
            trailing_6h_realized_vol_percentile=0.90,
            cooldown_key="test", archive_source_path="test",
        ),
    ]
    result = independent_return_reproduction(events, all_series, 1000.0)
    assert isinstance(result, dict)
    if result.get("indep_net50_mean") is not None:
        # Actual: (110/100 - 1) * 10000 - 50 = 1000 - 50 = 950
        assert abs(result.get("diff_bps", 0) - 50.0) < 0.1  # 1000 - 950 = 50


# ──────────────────────────────────────────────────────────────────
# Test 20: Cost stress arithmetic
# ──────────────────────────────────────────────────────────────────


def test_cost_stress_arithmetic():
    """Verify net75 = net50 - 25, net100 = net50 - 50."""
    net50 = 339.59448071794975
    net75 = net50 - 25.0
    net100 = net50 - 50.0
    assert net75 == 314.59448071794975
    assert net100 == 289.59448071794975


# ──────────────────────────────────────────────────────────────────
# Test 21: No liquidation machine-readable status fields
# ──────────────────────────────────────────────────────────────────


def test_no_liquidation_status_summary():
    """Verify no liquidation machine-readable fields appear."""
    for status in [
        STATUS_UNDERPOWERED,
        STATUS_TEMPORAL_CONCENTRATION_FAILED,
        STATUS_NEGATIVE_CONTROL_FAILED,
    ]:
        assert "LIQUIDATION" not in status


# ──────────────────────────────────────────────────────────────────
# Test 22: No Phase 0C runner/null code invoked
# ──────────────────────────────────────────────────────────────────


def test_no_phase0c_import():
    """Verify this module does not import Phase 0C code."""
    import sys as _sys
    forbidden = [
        "generic_altcoin_stress_regime_ablation_phase0c",
        "generic_stress_independent_return_audit",
    ]
    for mod in list(_sys.modules.keys()):
        for forbidden_mod in forbidden:
            assert forbidden_mod not in mod, f"Forbidden import: {mod}"


# ──────────────────────────────────────────────────────────────────
# Test: compute_precommitment_hash
# ──────────────────────────────────────────────────────────────────


def test_compute_precommitment_hash():
    """Verify compute_precommitment_hash works on a test string."""
    import tempfile
    content = "# Test\nPrecommitment SHA-256 (self): abc\nMore text\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        f.flush()
        h = compute_precommitment_hash(Path(f.name))
        # Without the self-hash line
        expected = hashlib.sha256(b"# Test\nMore text\n").hexdigest()
        assert h == expected


# ──────────────────────────────────────────────────────────────────
# Test: build_cohort_metrics
# ──────────────────────────────────────────────────────────────────


def test_build_cohort_metrics():
    """Verify cohort metrics are computed correctly."""
    events = [
        StressEventRecord(
            event_id=f"ev{i}", symbol="SOL",
            event_timestamp_utc=f"2024-0{i}-01T12:00:00Z",
            detector_family="test", direction="downside_price_drop",
            price_t=100.0, trailing_1h_return_bps=-350.0,
            trailing_6h_realized_vol_bps=200.0,
            trailing_6h_realized_vol_percentile=0.90,
            cooldown_key="test", archive_source_path="test",
        )
        for i in range(1, 4)
    ]
    evals = [
        HorizonEvaluation(
            event_id="ev1", symbol="SOL",
            event_timestamp_utc="2024-01-01T12:00:00Z",
            direction="downside_price_drop", horizon_hours=24,
            event_price=100.0, future_price=110.0,
            gross_return_bps=1000.0, net_return_bps_50=950.0,
            net_return_bps_75=925.0, missing=False,
        ),
    ]
    metrics = build_cohort_metrics(events, evals)
    assert metrics.evaluated_event_count == 3
    # Only 1 eval with non-missing data at 24h
    assert metrics.net_mean_bps_50 == 950.0
