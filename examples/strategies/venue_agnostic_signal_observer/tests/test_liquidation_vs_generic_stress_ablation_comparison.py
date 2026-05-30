"""Tests for liquidation vs generic stress ablation comparison."""

from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.liquidation_vs_generic_stress_ablation_comparison import (
    VERDICT_OI_SUPPORTED,
    VERDICT_OI_NOT_SUPPORTED,
    VERDICT_OI_HARMFUL,
    VERDICT_INCONCLUSIVE_LOW_OVERLAP,
    VERDICT_INCONCLUSIVE_COVERAGE,
    VERDICT_ERROR,
    verify_artifact,
    normalize_event,
    check_generic_cleanliness,
    compute_overlap,
    compute_cohort_metrics,
    classify_verdict,
    CohortMetrics,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _make_event(
    symbol: str = "SOL",
    ts: str = "2024-01-01T00:00:00Z",
    direction: str = "downside_price_drop",
    family: str = "generic_altcoin_stress_regime_ablation_phase0",
    price: float = 100.0,
    extra: dict | None = None,
) -> dict:
    ev = {
        "symbol": symbol,
        "event_timestamp_utc": ts,
        "event_direction": direction,
        "detector_family": family,
        "price_t": price,
        "event_id": f"{symbol}_{ts}",
    }
    if extra:
        ev.update(extra)
    return ev


# ------------------------------------------------------------------
# Cleanliness checks
# ------------------------------------------------------------------


def test_generic_event_cleanliness_clean():
    """Generic events with downside_price_drop should be clean."""
    events = [_make_event(direction="downside_price_drop")]
    issues = check_generic_cleanliness(events)
    assert len(issues) == 0


def test_generic_event_cleanliness_detects_liquidation_label():
    """Generic events with liquidation labels should be flagged."""
    events = [_make_event(direction="downside_liquidation_flush")]
    issues = check_generic_cleanliness(events)
    assert len(issues) >= 1
    assert "liquidation" in issues[0].lower()


def test_generic_event_cleanliness_detects_flush_side():
    """Generic events with flush_side should be flagged."""
    events = [_make_event(extra={"flush_side": "long_wipe"})]
    issues = check_generic_cleanliness(events)
    assert len(issues) >= 1


def test_generic_event_cleanliness_detects_btc_eth():
    """Events with BTC/ETH symbol should be flagged."""
    events = [_make_event(symbol="BTC")]
    issues = check_generic_cleanliness(events)
    assert len(issues) >= 1
    assert "BTC" in issues[0]


# ------------------------------------------------------------------
# Normalization
# ------------------------------------------------------------------


def test_normalize_event():
    """Normalized event should have correct fields."""
    ev = _make_event()
    norm = normalize_event(ev, "test_family")
    assert norm["detector_family"] == "test_family"
    assert norm["symbol"] == "SOL"
    assert norm["calendar_year"] == 2024
    assert norm["calendar_month"] == 1
    assert norm["calendar_quarter"] == 1


# ------------------------------------------------------------------
# Overlap computation
# ------------------------------------------------------------------


def test_compute_overlap_exact():
    """Exact same events should overlap."""
    liq = [_make_event(symbol="SOL", ts="2024-01-01T00:00:00Z")]
    gen = [_make_event(symbol="SOL", ts="2024-01-01T00:00:00Z")]
    result = compute_overlap(liq, gen)
    assert result["exact_overlap_count"] == 1
    assert result["liquidation_only_count"] == 0
    assert result["generic_only_count"] == 0


def test_compute_overlap_disjoint():
    """Non-overlapping events should have zero overlap."""
    liq = [_make_event(symbol="SOL", ts="2024-01-01T00:00:00Z")]
    gen = [_make_event(symbol="AAVE", ts="2024-01-02T00:00:00Z")]
    result = compute_overlap(liq, gen)
    assert result["exact_overlap_count"] == 0
    assert result["generic_only_count"] == 1
    assert result["liquidation_only_count"] == 1


def test_compute_overlap_empty():
    """Empty sets should produce zero overlap."""
    result = compute_overlap([], [])
    assert result["exact_overlap_count"] == 0


# ------------------------------------------------------------------
# Cohort metrics
# ------------------------------------------------------------------


def test_cohort_metrics_basic():
    """Cohort metrics should compute returns correctly."""
    price_series = {
        "SOL": [
            (datetime(2024, 1, 1, tzinfo=UTC), 100.0),
            (datetime(2024, 1, 2, tzinfo=UTC), 110.0),
        ],
    }
    events = [_make_event(symbol="SOL", ts="2024-01-01T00:00:00Z", price=100.0)]
    m = compute_cohort_metrics("test", events, price_series)
    assert m.name == "test"
    assert m.evaluated_count == 1
    assert m.net_mean_bps_50_24h is not None
    # Price goes 100 -> 110, gross = +1000 bps, net = +950 bps
    assert abs(m.net_mean_bps_50_24h - 950.0) < 1.0


def test_cohort_metrics_missing_forward():
    """Events without forward coverage should be counted as missing."""
    price_series = {"SOL": [(datetime(2024, 1, 1, tzinfo=UTC), 100.0)]}
    events = [_make_event(symbol="SOL", ts="2024-01-01T00:00:00Z", price=100.0)]
    m = compute_cohort_metrics("test", events, price_series)
    assert m.evaluated_count == 0
    assert m.missing_forward_24h == 1


# ------------------------------------------------------------------
# Verdict classification
# ------------------------------------------------------------------


def test_classify_verdict_generic_dominates():
    """Generic dominating liquidation should produce OI_NOT_SUPPORTED or OI_HARMFUL."""
    cohorts = [
        CohortMetrics("generic_all", 500, 450, 5, 300.0, 250.0, 200.0, 180.0, 0.62, 175.0, 150.0),
        CohortMetrics("generic_only", 400, 360, 4, 200.0, 180.0, 150.0, 130.0, 0.59, 125.0, 100.0),
        CohortMetrics("liquidation_all", 951, 951, 0, 175.0, 147.0, 125.0, 97.0, 0.57, 100.0, 75.0),
    ]
    verdict = classify_verdict(cohorts, liq_events_exist=True)
    # Generic mean (200) > 1.5x liquidation mean (125) = 187.5 -> passes generic_dominates
    # Generic_only (150) > 0.5 * liquidation_all (125) = 62.5 -> NOT harmful
    # So should be NOT_SUPPORTED
    assert verdict in {VERDICT_OI_NOT_SUPPORTED, VERDICT_OI_HARMFUL}


def test_classify_verdict_inconclusive_low_overlap():
    """Similar performance should be inconclusive."""
    cohorts = [
        CohortMetrics("generic_all", 500, 450, 5, 175.0, 147.0, 125.0, 97.0, 0.57, 100.0, 75.0),
        CohortMetrics("generic_only", 400, 360, 4, 170.0, 145.0, 120.0, 95.0, 0.56, 95.0, 70.0),
        CohortMetrics("liquidation_all", 951, 951, 0, 175.0, 147.0, 125.0, 97.0, 0.57, 100.0, 75.0),
    ]
    verdict = classify_verdict(cohorts, liq_events_exist=True)
    assert verdict == VERDICT_INCONCLUSIVE_LOW_OVERLAP


def test_classify_verdict_insufficient_coverage():
    """Too few evaluated events should be inconclusive."""
    cohorts = [
        CohortMetrics("generic_all", 50, 30, 20, 500.0, 400.0, 350.0, 300.0, 0.65, 325.0, 300.0),
        CohortMetrics("liquidation_all", 951, 951, 0, 175.0, 147.0, 125.0, 97.0, 0.57, 100.0, 75.0),
    ]
    verdict = classify_verdict(cohorts, liq_events_exist=True)
    assert verdict == VERDICT_INCONCLUSIVE_COVERAGE


# ------------------------------------------------------------------
# Artifact verification
# ------------------------------------------------------------------


def test_verify_artifact_missing(tmp_path):
    """Missing report should return MISSING status."""
    result = verify_artifact(tmp_path / "nonexistent")
    assert result["status"] == "MISSING"


# ------------------------------------------------------------------
# No prohibited strings
# ------------------------------------------------------------------


def test_no_order_strings():
    impl_path = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/liquidation_vs_generic_stress_ablation_comparison.py"
    if not impl_path.exists():
        pytest.skip("Implementation file not found")
    text = impl_path.read_text(encoding="utf-8")
    for token in ["submit_order", "place_order", "private_key", "api_key"]:
        assert token not in text


def test_no_live_trading_strings():
    impl_path = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/liquidation_vs_generic_stress_ablation_comparison.py"
    if not impl_path.exists():
        pytest.skip("Implementation file not found")
    text = impl_path.read_text(encoding="utf-8")
    exec_tokens = ["order_submission", "trade_execution_via_venue", "paper_trader_class"]
    for token in exec_tokens:
        assert token not in text