"""Tests for synthetic fixtures: positive lead-lag vs pure noise."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))


from examples.strategies.venue_agnostic_signal_observer.config import FeeModel
from examples.strategies.venue_agnostic_signal_observer.config import ObserverConfig
from examples.strategies.venue_agnostic_signal_observer.data_loading import (
    generate_synthetic_lead_lag,
)
from examples.strategies.venue_agnostic_signal_observer.data_loading import generate_synthetic_noise
from examples.strategies.venue_agnostic_signal_observer.observer import SignalObserver
from examples.strategies.venue_agnostic_signal_observer.signals import CrossMarketSignalGenerator
from examples.strategies.venue_agnostic_signal_observer.signals import SignalSourceConfig


def _run_synthetic(timestamps, source_prices, target_prices, fee_bps=5.0, slippage_bps=1.0):
    """Helper: generate signals from source and measure target forward returns."""
    cfg = ObserverConfig(
        signal_source=SignalSourceConfig(
            cross_market_source_venue="SRC",
            cross_market_source_instrument="BTC/USDT",
            cross_market_target_venue="TGT",
            cross_market_target_instrument="BTC/USD",
            cross_market_move_threshold_bps=20.0,
            cross_market_lookback_seconds=60.0,
            cross_market_cooldown_seconds=60.0,
        ),
        fee_model=FeeModel(fee_bps=fee_bps, slippage_bps=slippage_bps),
    )
    gen = CrossMarketSignalGenerator(cfg.signal_source)
    signals = gen.generate(timestamps, source_prices)
    assert len(signals) > 0, "Expected at least one cross-market signal"

    obs = SignalObserver(cfg)
    signals_list, results, summary = obs.run(
        signals=signals,
        target_timestamps=timestamps,
        target_prices=target_prices,
    )
    return signals_list, results, summary


def test_synthetic_positive_lead_lag():
    """Source jumps first, target follows with catch-up > 1.0 → positive net return."""
    ts, source_prices, target_prices = generate_synthetic_lead_lag(
        num_bars=10_000,
        dt_seconds=10.0,
        base_price=50_000.0,
        lag_seconds=30.0,
        target_catch_up_fraction=1.5,  # target over-shoots the move
        jump_bps=80.0,
        noise_std_bps=1.0,
    )
    _, results, summary = _run_synthetic(ts, source_prices, target_prices, fee_bps=5.0, slippage_bps=1.0)

    valid_nets = [r.net_return_bps for r in results if r.net_return_bps is not None]
    mean_net = sum(valid_nets) / len(valid_nets)
    assert mean_net > 0, f"Expected positive mean net return for lead-lag, got {mean_net:.4f} bps"

    # Check at least one horizon group has positive mean
    pos_horizon = any(
        h.get("mean_net_return_bps", 0) > 0
        for h in summary.results_by_horizon
    )
    assert pos_horizon, "At least one horizon should have positive mean net return"


def test_synthetic_noise_negative():
    """Uncorrelated source/target → no signal edge → negative net after fees."""
    ts, source_prices, target_prices = generate_synthetic_noise(
        num_bars=10_000,
        dt_seconds=10.0,
        base_price=50_000.0,
        noise_std_bps=5.0,
    )
    _, results, summary = _run_synthetic(ts, source_prices, target_prices, fee_bps=5.0, slippage_bps=1.0)

    valid_nets = [r.net_return_bps for r in results if r.net_return_bps is not None]
    mean_net = sum(valid_nets) / len(valid_nets)
    assert mean_net < 0, f"Expected negative mean net return for noise, got {mean_net:.4f} bps"

    # Win rate should be close to 50% or below (random walk + fees drain)
    for h in summary.results_by_horizon:
        wr = h.get("win_rate_after_fees", 0.5)
        assert wr <= 0.55, f"Win rate too high for noise in {h['horizon']}: {wr}"
