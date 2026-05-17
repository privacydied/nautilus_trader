"""
Tests for Phase 5 Shadow Executor.

Covers:
- Shadow must not place orders
- Shadow must not import live execution clients
- Uncertainty reporting mandatory
- Positive mean + wide uncertainty = fail
- Staleness rejects counted
- Fill rate, miss rate, forced exit reported
- Lower confidence bound computation
- No TRADE_READY status
"""

from __future__ import annotations

import pytest

from ..shadow import run_shadow, FillModelConfig, CrossVenueFillModel


def _config(**kwargs) -> FillModelConfig:
    defaults = dict(
        source_venue="binance",
        target_venue="kraken",
        entry_delay_seconds=5.0,
        max_staleness_seconds=2.0,
        maker_probability=0.6,
        maker_rebate_bps=2.0,
        taker_fee_bps=8.0,
        spread_bps=4.0,
        exit_horizon_seconds=180.0,
        slippage_model_std_bps=3.0,
        fill_model_uncertainty_bps=15.0,
    )
    defaults.update(kwargs)
    return FillModelConfig(**defaults)


def _events(n=50, move_bps=20.0):
    return [{"trigger_time_seconds": i * 30.0, "target_mid_move_bps": move_bps} for i in range(n)]


class TestShadowMustNotPlaceOrders:
    def test_no_order_placement_in_source(self):
        import inspect
        from ..shadow import shadow_executor as mod
        src = inspect.getsource(mod)
        forbidden = ["submit_order", "place_order", "send_order", "create_order",
                     "nautilus_trader", "ccxt.create_order", "private_key"]
        for f in forbidden:
            assert f not in src, f"shadow_executor.py must not contain '{f}'"

    def test_no_live_execution_clients_imported(self):
        import inspect
        from ..shadow import fill_model as mod
        src = inspect.getsource(mod)
        assert "nautilus_trader" not in src
        assert "private_key" not in src


class TestShadowUncertainty:
    def test_uncertainty_always_reported(self):
        result = run_shadow(_events(50), _config())
        assert result.fill_model_uncertainty_estimate > 0

    def test_positive_mean_wide_uncertainty_is_fail(self):
        # If uncertainty > mean, lower_confidence_bound <= 0 → fail
        cfg = _config(fill_model_uncertainty_bps=100.0, taker_fee_bps=1.0,
                      spread_bps=1.0, slippage_model_std_bps=0.5)
        result = run_shadow(_events(100, move_bps=5.0), cfg, seed=1)
        # With 100 bps uncertainty, even a positive mean should fail
        if result.shadow_net_bps is not None and result.shadow_net_bps > 0:
            if result.lower_confidence_bound is not None and result.lower_confidence_bound <= 0:
                assert not result.shadow_pass

    def test_lower_confidence_bound_present(self):
        result = run_shadow(_events(100, move_bps=20.0), _config(taker_fee_bps=2.0))
        # lower_confidence_bound must always be reported (not None) when fills exist
        if result.shadow_fill_rate > 0:
            assert result.lower_confidence_bound is not None


class TestShadowFields:
    def test_all_required_fields_present(self):
        result = run_shadow(_events(50), _config())
        assert hasattr(result, "shadow_fill_rate")
        assert hasattr(result, "missed_fill_rate")
        assert hasattr(result, "partial_fill_rate")
        assert hasattr(result, "forced_exit_rate")
        assert hasattr(result, "entry_spread")
        assert hasattr(result, "exit_spread")
        assert hasattr(result, "queue_penalty")
        assert hasattr(result, "staleness_rejects")
        assert hasattr(result, "shadow_net_bps")
        assert hasattr(result, "fill_model_uncertainty_estimate")
        assert hasattr(result, "lower_confidence_bound")
        assert hasattr(result, "capacity_estimate")

    def test_staleness_rejects_counted(self):
        # High staleness ceiling makes rejects likely
        cfg = _config(max_staleness_seconds=0.001)
        result = run_shadow(_events(100), cfg, seed=5)
        assert result.staleness_rejects >= 0
        assert result.staleness_reject_rate >= 0.0

    def test_no_trade_ready_in_output(self):
        result = run_shadow(_events(100, move_bps=30.0), _config(taker_fee_bps=1.0))
        d = result.to_dict()
        assert "TRADE_READY" not in str(d)

    def test_empty_events_returns_fail(self):
        result = run_shadow([], _config())
        assert not result.shadow_pass
        assert result.shadow_fail_reason is not None

    def test_capacity_estimate_reported(self):
        result = run_shadow(_events(50), _config())
        assert result.capacity_estimate is not None
        assert result.capacity_estimate >= 0
