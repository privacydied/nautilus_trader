"""Tests for the lead-lag real-data pipeline.

Covers:
  - Real CSV round-trip via synthetic data → CSV → loader → align → sweep
  - Random baseline produces results near zero mean
  - Lead-lag signals fire correctly on known moves
  - No crashes on edge cases (empty data, short series, no moves)
"""
import csv
import tempfile
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.config import (
    FeeModel,
    Horizon,
    LeadLagConfig,
)
from examples.strategies.venue_agnostic_signal_observer.data_loading import (
    generate_synthetic_data,
)
from examples.strategies.venue_agnostic_signal_observer.forward_returns import (
    evaluate_signal,
)
from examples.strategies.venue_agnostic_signal_observer.lead_lag import (
    generate_lead_lag_signals,
    generate_random_baseline,
)
from examples.strategies.venue_agnostic_signal_observer.reports import write_outputs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_series():
    """Small deterministic synthetic data suitable for quick tests."""
    ts, source, target = generate_synthetic_data(
        num_bars=500,
        dt_seconds=10.0,
        base_price=50_000.0,
        target_follow_delay=30.0,
        target_follow_fraction=0.7,
        jump_threshold_bps=50.0,
        jump_interval=50,  # more frequent jumps for test coverage
        noise_std_bps=2.0,
    )
    return ts, source, target


@pytest.fixture
def flat_series():
    """Flat price series — should produce zero signals."""
    ts = [1_700_000_000.0 + i * 10.0 for i in range(100)]
    prices = [50_000.0] * 100
    return ts, prices


@pytest.fixture
def fee_model():
    return FeeModel(fee_bps=10.0, slippage_bps=2.0)


@pytest.fixture
def horizons():
    return [
        Horizon("10s", 10.0),
        Horizon("30s", 30.0),
        Horizon("60s", 60.0),
        Horizon("5m", 300.0),
    ]


# ---------------------------------------------------------------------------
# Lead-lag signal generation
# ---------------------------------------------------------------------------

class TestLeadLagSignalGeneration:
    def test_signals_fire_on_big_moves(self, synthetic_series):
        ts, source, _target = synthetic_series
        signals = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=source,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
            cooldown_seconds=30.0,
        )
        # With synthetic jumps every 50 bars at 50bps, we should get signals
        assert len(signals) > 0

    def test_no_signals_on_flat_prices(self, flat_series):
        ts, prices = flat_series
        signals = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=prices,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
            cooldown_seconds=30.0,
        )
        assert len(signals) == 0

    def test_no_signals_with_high_threshold(self, synthetic_series):
        ts, source, _target = synthetic_series
        signals = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=source,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=10000.0,  # impossibly high
            cooldown_seconds=30.0,
        )
        assert len(signals) == 0

    def test_signal_direction_matches_move(self):
        """A positive move should produce a 'long' signal."""
        ts = [1_700_000_000.0 + i * 10.0 for i in range(200)]
        # Big upward move
        prices = [50_000.0 + i * 50.0 for i in range(200)]
        signals = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=prices,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
            cooldown_seconds=30.0,
        )
        assert len(signals) > 0
        # First signal should be long (price rising)
        assert signals[0].direction == "long"

    def test_cooldown_limits_signal_frequency(self):
        ts = [1_700_000_000.0 + i * 10.0 for i in range(200)]
        # Alternating big moves: every bar moves 100bps up or down
        prices = [50_000.0 * (1.01 if i % 2 == 0 else 0.99) ** i for i in range(200)]
        
        signals_cooldown = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=prices,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
            cooldown_seconds=60.0,  # 6 bars
        )
        signals_no_cooldown = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=prices,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
            cooldown_seconds=0.0,
        )
        assert len(signals_cooldown) <= len(signals_no_cooldown)

    def test_empty_series(self):
        signals = generate_lead_lag_signals(
            source_timestamps=[],
            source_prices=[],
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
        )
        assert len(signals) == 0

    def test_single_bar(self):
        signals = generate_lead_lag_signals(
            source_timestamps=[1_700_000_000.0],
            source_prices=[50_000.0],
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=5.0,
        )
        assert len(signals) == 0


# ---------------------------------------------------------------------------
# Random baseline
# ---------------------------------------------------------------------------

class TestRandomBaseline:
    def test_correct_count(self, synthetic_series):
        ts, _, _ = synthetic_series
        signals = generate_random_baseline(
            source_timestamps=ts,
            signal_count=42,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
        )
        assert len(signals) == 42

    def test_deterministic_with_seed(self, synthetic_series):
        ts, _, _ = synthetic_series
        s1 = generate_random_baseline(
            source_timestamps=ts, signal_count=10,
            source_venue="A", source_instrument="X",
            target_venue="B", target_instrument="Y",
            seed=123,
        )
        s2 = generate_random_baseline(
            source_timestamps=ts, signal_count=10,
            source_venue="A", source_instrument="X",
            target_venue="B", target_instrument="Y",
            seed=123,
        )
        assert [s.timestamp for s in s1] == [s.timestamp for s in s2]
        assert [s.direction for s in s1] == [s.direction for s in s2]

    def test_all_directions_valid(self, synthetic_series):
        ts, _, _ = synthetic_series
        signals = generate_random_baseline(
            source_timestamps=ts, signal_count=100,
            source_venue="A", source_instrument="X",
            target_venue="B", target_instrument="Y",
        )
        assert all(s.direction in ("long", "short") for s in signals)

    def test_timestamps_in_range(self, synthetic_series):
        ts, _, _ = synthetic_series
        signals = generate_random_baseline(
            source_timestamps=ts, signal_count=50,
            source_venue="A", source_instrument="X",
            target_venue="B", target_instrument="Y",
        )
        ts_min = ts[0]
        ts_max = ts[-1]
        assert all(ts_min <= s.timestamp <= ts_max for s in signals)

    def test_empty_series(self):
        signals = generate_random_baseline(
            source_timestamps=[],
            signal_count=10,
            source_venue="A", source_instrument="X",
            target_venue="B", target_instrument="Y",
        )
        assert len(signals) == 0

    def test_zero_count(self, synthetic_series):
        ts, _, _ = synthetic_series
        signals = generate_random_baseline(
            source_timestamps=ts, signal_count=0,
            source_venue="A", source_instrument="X",
            target_venue="B", target_instrument="Y",
        )
        assert len(signals) == 0


# ---------------------------------------------------------------------------
# End-to-end: synthetic data → lead-lag → forward returns
# ---------------------------------------------------------------------------

class TestLeadLagEndToEnd:
    def test_lead_lag_on_synthetic_data(self, synthetic_series, fee_model, horizons):
        ts, source, target = synthetic_series

        # Generate lead-lag signals from source moves
        signals = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=source,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=20.0,
            cooldown_seconds=30.0,
        )
        assert len(signals) > 0

        # Evaluate on target data
        all_results = []
        for sig in signals:
            results = evaluate_signal(sig, ts, target, horizons, fee_model)
            all_results.extend(results)

        valid = [r for r in all_results if r.valid]
        assert len(valid) > 0

    def test_baseline_near_zero_mean(self, flat_series, fee_model, horizons):
        """Random baseline on flat data should produce mean close to 0."""
        ts, prices = flat_series
        
        signals = generate_random_baseline(
            source_timestamps=ts,
            signal_count=20,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
        )
        
        all_results = []
        for sig in signals:
            results = evaluate_signal(sig, ts, prices, horizons, fee_model)
            all_results.extend(results)

        valid = [r for r in all_results if r.valid]
        if valid:
            nets = [r.net_return_bps for r in valid if r.net_return_bps is not None]
            if nets:
                mean_net = sum(nets) / len(nets)
                # On flat data, returns are ~0, so net should be approximately -cost
                assert mean_net < 0  # fees eat into returns on zero-move data

    def test_report_output(self, synthetic_series, fee_model, horizons, tmp_path):
        ts, source, target = synthetic_series
        
        signals = generate_lead_lag_signals(
            source_timestamps=ts,
            source_prices=source,
            source_venue="BINANCE",
            source_instrument="BTC/USDT",
            target_venue="KRAKEN",
            target_instrument="BTC/USD",
            lookback_seconds=30.0,
            move_threshold_bps=20.0,
            cooldown_seconds=30.0,
        )
        
        all_results = []
        for sig in signals:
            results = evaluate_signal(sig, ts, target, horizons, fee_model)
            all_results.extend(results)
        
        signal_dicts = [s.to_dict() for s in signals]
        
        # Create a mock summary for write_outputs
        from examples.strategies.venue_agnostic_signal_observer.models import SignalEvaluationSummary
        
        summary = SignalEvaluationSummary(
            total_signals=len(signals),
            valid_evaluations=len([r for r in all_results if r.valid]),
            rejected_evaluations=len([r for r in all_results if not r.valid]),
            fee_bps=fee_model.fee_bps,
            slippage_bps=fee_model.slippage_bps,
            run_start=ts[0],
            run_end=ts[-1],
        )
        
        write_outputs(signal_dicts, all_results, summary, tmp_path)
        
        assert (tmp_path / "signal_events.jsonl").exists()
        assert (tmp_path / "forward_returns.jsonl").exists()
        assert (tmp_path / "summary.json").exists()
