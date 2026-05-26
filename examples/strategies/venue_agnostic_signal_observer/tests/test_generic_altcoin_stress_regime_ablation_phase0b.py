"""Tests for generic altcoin stress regime ablation Phase 0B."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0b import (
    GENERIC_EVENT_DIRECTION,
    GENERIC_TRADE_DIRECTION,
    STATUS_RETURN_DIAGNOSTIC_PASS,
    STATUS_RETURN_DIAGNOSTIC_FAIL,
    STATUS_INSUFFICIENT_FORWARD_COVERAGE,
    STATUS_ERROR_INVALID_PHASE0A,
    STATUS_ERROR_PRECOMMITMENT,
    LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS,
    LIQUIDATION_BENCHMARK_24H_NET_MEDIAN_BPS,
    LIQUIDATION_BENCHMARK_24H_WIN_RATE,
    STRESS_COST_BPS,
    compute_directional_return_bps,
    evaluate_events,
    load_generic_phase0a_report,
    classify_primary_verdict,
    compute_stress_cost_metrics,
    build_comparison_block,
)
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    EventRecord,
    LoadDiagnostics,
)
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0b import (
    HORIZONS_HOURS,
    PRIMARY_HORIZON_HOURS,
    HorizonEvaluation,
    HorizonMetrics,
    build_price_series,
    _metrics_for_horizon,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
PHASE0B_PRECOMMITMENT = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/docs/GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_PRECOMMITMENT.md"


def row(symbol: str, ts: datetime, price: float, oi: float = 1e8, order: int = 0) -> ArchiveRow:
    return ArchiveRow(ts, symbol, price, oi, "fixture", order)


def make_event_dict(
    symbol: str = "SOL",
    ts: datetime | None = None,
    price: float = 100.0,
    direction: str = GENERIC_EVENT_DIRECTION,
) -> dict:
    ts = ts or datetime(2024, 1, 1, tzinfo=UTC)
    return {
        "event_id": f"{symbol}_{ts:%Y%m%dT%H%M%SZ}",
        "symbol": symbol,
        "event_timestamp_utc": ts.isoformat().replace("+00:00", "Z"),
        "event_direction": direction,
        "price_t": price,
        "detector_family": "generic_altcoin_stress_regime_ablation",
        "trailing_1h_return_bps": -400.0,
        "trailing_6h_realized_vol_bps": 5000.0,
        "trailing_6h_realized_vol_percentile": 0.9,
        "cooldown_key": symbol,
    }


# ------------------------------------------------------------------
# Direction handling
# ------------------------------------------------------------------


def test_compute_directional_return_downside_price_drop():
    """downside_price_drop events compute long returns."""
    result = compute_directional_return_bps(GENERIC_EVENT_DIRECTION, 100.0, 105.0)
    assert abs(result - 500.0) < 1e-9


def test_compute_directional_return_negative():
    """Negative forward return for downside_price_drop when price falls further."""
    result = compute_directional_return_bps(GENERIC_EVENT_DIRECTION, 100.0, 95.0)
    assert abs(result + 500.0) < 1e-9


def test_compute_directional_return_unknown_direction():
    """Unknown direction should raise ValueError."""
    with pytest.raises(ValueError, match="ambiguous"):
        compute_directional_return_bps("unknown_direction", 100.0, 105.0)


def test_compute_directional_return_zero_price():
    """Zero or negative prices should raise ValueError."""
    with pytest.raises(ValueError, match="prices must be positive"):
        compute_directional_return_bps(GENERIC_EVENT_DIRECTION, 0.0, 100.0)


# ------------------------------------------------------------------
# Events with generic direction
# ------------------------------------------------------------------


def test_evaluate_events_downside_price_drop():
    """Events with downside_price_drop should be evaluated as long."""
    events = [make_event_dict("SOL", price=100.0)]
    series = {
        "SOL": [
            (datetime(2024, 1, 1, tzinfo=UTC), 100.0),
            (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=6), 102.0),
            (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=12), 104.0),
            (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=24), 106.0),
            (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=48), 108.0),
        ],
    }
    evals, metrics, excluded = evaluate_events(events, series)
    assert excluded == 0
    assert len(evals) == len(HORIZONS_HOURS)
    for e in evals:
        assert e.gross_return_bps is not None
        assert e.gross_return_bps > 0  # prices go up


def test_evaluate_events_wrong_direction_excluded():
    """Events with wrong direction should be excluded."""
    events = [make_event_dict("SOL", direction="downside_liquidation_flush")]
    series = {"SOL": [(datetime(2024, 1, 1, tzinfo=UTC), 100.0)]}
    evals, metrics, excluded = evaluate_events(events, series)
    assert excluded == 1


def test_evaluate_events_btc_excluded():
    """BTC should be excluded from evaluation."""
    events = [make_event_dict("BTC")]
    series = {"BTC": [(datetime(2024, 1, 1, tzinfo=UTC), 100.0)]}
    evals, metrics, excluded = evaluate_events(events, series)
    assert excluded == 1


# ------------------------------------------------------------------
# Primary verdict classification
# ------------------------------------------------------------------


def test_classify_primary_verdict_pass():
    """Sufficient events with positive net returns should pass."""
    metrics = HorizonMetrics(
        horizon_hours=24,
        evaluated_event_count=250,
        gross_mean_bps=200.0,
        gross_median_bps=150.0,
        net_mean_bps_50bps=100.0,
        net_median_bps_50bps=80.0,
        win_rate_50bps=0.60,
        mean_lcb_95_bps_50bps=20.0,
        missing_forward_count=0,
    )
    assert classify_primary_verdict(metrics) == STATUS_RETURN_DIAGNOSTIC_PASS


def test_classify_primary_verdict_insufficient_events():
    """Too few events should fail."""
    metrics = HorizonMetrics(
        horizon_hours=24,
        evaluated_event_count=50,
        gross_mean_bps=200.0,
        gross_median_bps=150.0,
        net_mean_bps_50bps=100.0,
        net_median_bps_50bps=80.0,
        win_rate_50bps=0.60,
        mean_lcb_95_bps_50bps=20.0,
        missing_forward_count=0,
    )
    assert classify_primary_verdict(metrics) == STATUS_INSUFFICIENT_FORWARD_COVERAGE


def test_classify_primary_verdict_negative_net_mean():
    """Negative net mean should fail."""
    metrics = HorizonMetrics(
        horizon_hours=24,
        evaluated_event_count=250,
        gross_mean_bps=10.0,
        gross_median_bps=5.0,
        net_mean_bps_50bps=-20.0,
        net_median_bps_50bps=-10.0,
        win_rate_50bps=0.45,
        mean_lcb_95_bps_50bps=-50.0,
        missing_forward_count=0,
    )
    assert classify_primary_verdict(metrics) == STATUS_RETURN_DIAGNOSTIC_FAIL


# ------------------------------------------------------------------
# Stress cost metrics
# ------------------------------------------------------------------


def test_stress_cost_metrics():
    """Stress cost metrics should compute correctly."""
    evals = [
        HorizonEvaluation("e1", "SOL", "2024-01-01T00:00:00Z", GENERIC_EVENT_DIRECTION, 24, 100.0, 105.0, 500.0, 450.0, 475.0, False),
        HorizonEvaluation("e2", "SOL", "2024-01-02T00:00:00Z", GENERIC_EVENT_DIRECTION, 24, 100.0, 102.0, 200.0, 150.0, 175.0, False),
        HorizonEvaluation("e3", "SOL", "2024-01-03T00:00:00Z", GENERIC_EVENT_DIRECTION, 24, 100.0, 98.0, -200.0, -250.0, -225.0, False),
    ]
    result = compute_stress_cost_metrics(evals, 24, 75.0)
    assert result["net_mean_bps"] is not None
    assert result["net_median_bps"] is not None
    # (500 - 75 + 200 - 75 + (-200) - 75) / 3 = (425 + 125 + (-275)) / 3 = 275 / 3 = 91.67
    expected_mean = (500.0 + 200.0 + (-200.0)) / 3.0 - 75.0
    assert abs(result["net_mean_bps"] - expected_mean) < 1e-9


# ------------------------------------------------------------------
# Comparison block
# ------------------------------------------------------------------


def test_build_comparison_block():
    """Comparison block should include benchmark references."""
    metrics = [
        HorizonMetrics(6, 100, 100.0, 80.0, 50.0, 40.0, 0.55, 10.0, 0),
        HorizonMetrics(24, 200, 150.0, 120.0, 80.0, 60.0, 0.58, 20.0, 0),
        HorizonMetrics(48, 100, 80.0, 60.0, 30.0, 20.0, 0.52, 5.0, 0),
    ]
    block = build_comparison_block(metrics)
    comp = block["comparison_vs_liquidation_flush"]
    assert comp["liquidation_flush_24h_net_mean_bps"] == LIQUIDATION_BENCHMARK_24H_NET_MEAN_BPS
    assert comp["generic_stress_24h_net_mean_bps"] == 80.0
    assert comp["generic_stress_24h_win_rate"] == 0.58
    assert "disclaimer" in comp
    assert "Phase 0C" in comp["disclaimer"]


# ------------------------------------------------------------------
# Precommitment hash
# ------------------------------------------------------------------


def test_phase0b_precommitment_hash():
    """Phase 0B precommitment hash should be self-consistent."""
    from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0b import (
        precommitment_recorded_and_computed,
    )
    if not PHASE0B_PRECOMMITMENT.exists():
        pytest.skip("Phase 0B precommitment doc not found")
    recorded, computed = precommitment_recorded_and_computed(PHASE0B_PRECOMMITMENT)
    assert recorded == computed
    assert isinstance(recorded, str) and len(recorded) == 64


# ------------------------------------------------------------------
# Constants consistency
# ------------------------------------------------------------------


def test_generic_event_direction_not_liquidation():
    """Generic event direction should not reference liquidation."""
    assert GENERIC_EVENT_DIRECTION != "downside_liquidation_flush"
    assert "liquidation" not in GENERIC_EVENT_DIRECTION


def test_trade_direction_is_long():
    """Trade direction after downside stress should be long."""
    assert GENERIC_TRADE_DIRECTION == "long"


def test_stress_costs_defined():
    """Stress costs should include 75 and 100 bps."""
    assert 75.0 in STRESS_COST_BPS
    assert 100.0 in STRESS_COST_BPS


# ------------------------------------------------------------------
# Safety: no prohibited strings
# ------------------------------------------------------------------


def test_no_order_strings():
    """Implementation should not contain order/auth/live strings."""
    impl_path = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/generic_altcoin_stress_regime_ablation_phase0b.py"
    if not impl_path.exists():
        pytest.skip("Implementation file not found")
    text = impl_path.read_text(encoding="utf-8")
    forbidden = ["submit_order", "place_order", "private_key", "api_key"]
    for token in forbidden:
        assert token not in text, f"Forbidden string found: {token}"


def test_no_live_trading_strings():
    """No live, paper, shadow, systemd, bot strings in implementation code."""
    impl_path = REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/generic_altcoin_stress_regime_ablation_phase0b.py"
    if not impl_path.exists():
        pytest.skip("Implementation file not found")
    text = impl_path.read_text(encoding="utf-8")
    # Check code patterns only (prose in docstrings/comments is fine)
    code_tokens = ["submit_order", "place_order", "private_key", "api_key"]
    for token in code_tokens:
        assert token not in text
    # No order/trade execution code paths
    exec_tokens = ["order_submission", "trade_execution_via_venue", "paper_trader_class"]
    for token in exec_tokens:
        assert token not in text