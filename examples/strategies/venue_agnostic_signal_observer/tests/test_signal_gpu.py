"""Tests for GPU-accelerated trade-flow impulse signal generation.

Covers parity against CPU for all signal types, edge cases,
device parsing, and determinism.
"""

from __future__ import annotations

import copy
import math
import random
import sys
from pathlib import Path

import pytest

from ..tick_models import TradeTickLite, TickSignalEvent
from ..trade_flow_impulse import (
    TradeFlowImpulseConfig,
    TradeFlowImpulseSignalGenerator,
)
from ..trade_flow_impulse_gpu import (
    notional_burst_gpu,
    large_trade_gpu,
    signed_imbalance_gpu,
    generate_signals_gpu,
    check_cuda_available,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_AVAIL, _REASON = check_cuda_available("cuda:0")
_HAS_CUDA = _AVAIL


def _make_trade(
    ts_event: int, price: float, size: float, side: str = "buy",
    venue: str = "binance_perp", symbol: str = "BTC/USDT",
) -> TradeTickLite:
    return TradeTickLite(
        ts_event=ts_event, venue=venue, symbol=symbol,
        price=price, size=size, side=side,
    )


@pytest.fixture
def cfg():
    return TradeFlowImpulseConfig(
        source_venue="binance_perp",
        target_venue="kraken",
        symbol="BTC/USD",
        asset="BTC",
        flow_lookbacks_ms=[1000, 5000],
        baseline_window_ms=60000,
        signal_types=["notional_burst", "large_trade", "signed_imbalance"],
        cooldown_ms=10000,
    )


@pytest.fixture
def small_trades():
    random.seed(42)
    trades = []
    for i in range(100):
        trades.append(_make_trade(
            ts_event=1_000_000_000 + i * 100_000_000,  # 100ms intervals
            price=100.0 + random.uniform(-0.5, 0.5),
            size=random.uniform(0.1, 5.0),
            side=random.choice(["buy", "sell"]),
        ))
    return trades


@pytest.fixture
def dense_trades():
    """High-density burst scenario to trigger notional_burst."""
    trades = []
    base_ts = 1_000_000_000
    # Quiet period: 50 trades at low volume
    for i in range(100):
        trades.append(_make_trade(
            ts_event=base_ts + i * 50_000_000,
            price=100.0, size=0.1, side="buy",
        ))
    # Burst period: 50 trades at high volume
    for i in range(100):
        trades.append(_make_trade(
            ts_event=base_ts + 100 * 50_000_000 + i * 5_000_000,
            price=100.5 + i * 0.01, size=5.0, side="buy",
        ))
    return trades


@pytest.fixture
def imbalance_trades():
    """Strong buy imbalance scenario."""
    trades = []
    base_ts = 1_000_000_000
    for i in range(200):
        side = "buy" if i < 160 else "sell"  # 80% buy
        trades.append(_make_trade(
            ts_event=base_ts + i * 10_000_000,
            price=100.0 + i * 0.001, size=1.0, side=side,
        ))
    return trades


# ---------------------------------------------------------------------------
# GPU availability
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
def test_cuda_available():
    """CUDA is available on this system."""
    ok, reason = check_cuda_available("cuda:0")
    assert ok, f"CUDA should be available: {reason}"


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
def test_gpu_imports():
    """GPU signal module imports cleanly with torch."""
    from ..trade_flow_impulse_gpu import notional_burst_gpu, large_trade_gpu, signed_imbalance_gpu
    assert callable(notional_burst_gpu)
    assert callable(large_trade_gpu)
    assert callable(signed_imbalance_gpu)


# ---------------------------------------------------------------------------
# CPU vs GPU parity
# ---------------------------------------------------------------------------


def _cpu_signals(trades, cfg, signal_type):
    """Run CPU signal generation for a single signal type."""
    from dataclasses import replace
    if signal_type:
        single_cfg = replace(cfg, signal_types=[signal_type])
    else:
        single_cfg = cfg
    gen = TradeFlowImpulseSignalGenerator(single_cfg)
    return gen.generate(trades)


def _signal_fields_match(cpu_sigs, gpu_sigs) -> list[str]:
    """Compare two signal lists for field-level parity. Return mismatches."""
    mismatches = []
    cpu_by_lb = {}
    for s in cpu_sigs:
        lb = s.metadata.get("lookback_ms", s.lookback_ms) if s.metadata else s.lookback_ms
        cpu_by_lb.setdefault(lb, []).append(s)
    gpu_by_lb = {}
    for s in gpu_sigs:
        lb = s.metadata.get("lookback_ms", s.lookback_ms) if s.metadata else s.lookback_ms
        gpu_by_lb.setdefault(lb, []).append(s)

    all_lbs = set(cpu_by_lb) | set(gpu_by_lb)
    for lb in sorted(all_lbs):
        cpu_list = cpu_by_lb.get(lb, [])
        gpu_list = gpu_by_lb.get(lb, [])
        if len(cpu_list) != len(gpu_list):
            mismatches.append(f"lb={lb}ms count: cpu={len(cpu_list)} gpu={len(gpu_list)}")
            continue
        for ci, gi in zip(cpu_list, gpu_list):
            for field in ["ts_event", "direction", "lookback_ms", "signal_type"]:
                cv = getattr(ci, field)
                gv = getattr(gi, field)
                if cv != gv:
                    mismatches.append(f"lb={lb} {field}: cpu={cv} gpu={gv}")
            # Compare metadata (id, signal_id will differ)
            if ci.metadata and gi.metadata:
                for k in ci.metadata:
                    if k == "side_source":
                        continue
                    cv = ci.metadata.get(k)
                    gv = gi.metadata.get(k)
                    if cv != gv and not (isinstance(cv, (int, float)) and isinstance(gv, (int, float))
                                          and abs(cv - gv) < 0.01):
                        mismatches.append(f"lb={lb} metadata.{k}: cpu={cv} gpu={gv}")
    return mismatches


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
class TestParity:
    def test_notional_burst_parity(self, dense_trades, cfg):
        """notional_burst GPU matches CPU."""
        cpu = _cpu_signals(dense_trades, cfg, "notional_burst")
        gpu = notional_burst_gpu(dense_trades, cfg, device="cuda:0")
        mismatches = _signal_fields_match(cpu, gpu)
        assert not mismatches, f"notional_burst mismatches: {mismatches}"

    def test_large_trade_parity(self, dense_trades, cfg):
        """large_trade GPU matches CPU."""
        cpu = _cpu_signals(dense_trades, cfg, "large_trade")
        gpu = large_trade_gpu(dense_trades, cfg, device="cuda:0")
        mismatches = _signal_fields_match(cpu, gpu)
        assert not mismatches, f"large_trade mismatches: {mismatches}"

    def test_signed_imbalance_parity(self, imbalance_trades, cfg):
        """signed_imbalance GPU matches CPU."""
        cpu = _cpu_signals(imbalance_trades, cfg, "signed_imbalance")
        gpu = signed_imbalance_gpu(imbalance_trades, cfg, device="cuda:0")
        mismatches = _signal_fields_match(cpu, gpu)
        assert not mismatches, f"signed_imbalance mismatches: {mismatches}"

    def test_combined_parity(self, dense_trades, cfg):
        """Combined GPU signal generation matches CPU."""
        # CPU with all three signal types
        cpu = _cpu_signals(dense_trades, cfg, None)
        # GPU generate_signals_gpu
        gpu = generate_signals_gpu(dense_trades, cfg, device="cuda:0")
        mismatches = _signal_fields_match(cpu, gpu)
        assert not mismatches, f"combined mismatches: {mismatches}"

    def test_determinism(self, dense_trades, cfg):
        """GPU produces same result on repeated calls."""
        gpu1 = notional_burst_gpu(dense_trades, cfg, device="cuda:0")
        gpu2 = notional_burst_gpu(dense_trades, cfg, device="cuda:0")
        assert len(gpu1) == len(gpu2)
        for s1, s2 in zip(gpu1, gpu2):
            assert s1.ts_event == s2.ts_event
            assert s1.direction == s2.direction


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
class TestEdgeCases:
    def test_empty_trades(self, cfg):
        """Empty trade list returns empty."""
        assert generate_signals_gpu([], cfg, device="cuda:0") == []
        assert notional_burst_gpu([], cfg, device="cuda:0") == []
        assert large_trade_gpu([], cfg, device="cuda:0") == []
        assert signed_imbalance_gpu([], cfg, device="cuda:0") == []

    def test_one_trade(self, cfg):
        """Single trade returns empty."""
        t = [_make_trade(1_000_000_000, 100.0, 1.0)]
        assert generate_signals_gpu(t, cfg, device="cuda:0") == []

    def test_zero_price(self, cfg):
        """Zero price trades are handled gracefully."""
        trades = [_make_trade(1_000_000_000 + i * 10_000_000, 0.0, 1.0)
                  for i in range(50)]
        result = generate_signals_gpu(trades, cfg, device="cuda:0")
        # Should not crash; zero-price trades produce zero notionals
        assert isinstance(result, list)

    def test_negative_price(self, cfg):
        """Negative price trades are handled gracefully."""
        trades = [_make_trade(1_000_000_000 + i * 10_000_000, -50.0, 1.0)
                  for i in range(50)]
        result = generate_signals_gpu(trades, cfg, device="cuda:0")
        assert isinstance(result, list)

    def test_nan_price(self, cfg):
        """NaN price trades are handled gracefully."""
        trades = [_make_trade(1_000_000_000, float("nan"), 1.0),
                  _make_trade(2_000_000_000, 100.0, 1.0)]
        result = generate_signals_gpu(trades, cfg, device="cuda:0")
        assert isinstance(result, list)

    def test_nan_size(self, cfg):
        """NaN size trades are handled gracefully."""
        trades = [_make_trade(1_000_000_000, 100.0, float("nan")),
                  _make_trade(2_000_000_000, 100.0, 1.0)]
        result = generate_signals_gpu(trades, cfg, device="cuda:0")
        assert isinstance(result, list)

    def test_inf_price(self, cfg):
        """Inf price trades are handled gracefully."""
        trades = [_make_trade(1_000_000_000, float("inf"), 1.0),
                  _make_trade(2_000_000_000, 100.0, 1.0)]
        result = generate_signals_gpu(trades, cfg, device="cuda:0")
        assert isinstance(result, list)

    def test_inf_size(self, cfg):
        """Inf size trades are handled gracefully."""
        trades = [_make_trade(1_000_000_000, 100.0, float("inf")),
                  _make_trade(2_000_000_000, 100.0, 1.0)]
        result = generate_signals_gpu(trades, cfg, device="cuda:0")
        assert isinstance(result, list)

    def test_mixed_finite_and_nonfinite(self, cfg):
        """Mix of finite and non-finite prices/sizes."""
        trades = []
        for i in range(50):
            if i % 5 == 0:
                trades.append(_make_trade(1_000_000_000 + i * 10_000_000, float("nan"), 1.0))
            elif i % 5 == 1:
                trades.append(_make_trade(1_000_000_000 + i * 10_000_000, 100.0, float("inf")))
            elif i % 5 == 2:
                trades.append(_make_trade(1_000_000_000 + i * 10_000_000, -1.0, 1.0))
            else:
                trades.append(_make_trade(1_000_000_000 + i * 10_000_000, 100.0 + i, 1.0))
        # CPU should also handle these
        cpu = _cpu_signals(trades, cfg, None)
        gpu = generate_signals_gpu(trades, cfg, device="cuda:0")
        mismatches = _signal_fields_match(cpu, gpu)
        assert not mismatches, f"mixed finite mismatches: {mismatches}"


# ---------------------------------------------------------------------------
# Device parsing
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
def test_check_cuda_availability():
    """check_cuda_available returns (True, reason) for valid device."""
    ok, reason = check_cuda_available("cuda:0")
    assert ok
    assert "cuda_available" in reason


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
def test_invalid_device_string():
    """Invalid device string returns (False, reason)."""
    ok, reason = check_cuda_available("cuda:garbage")
    assert not ok
    assert "invalid_device_string" in reason


@pytest.mark.skipif(not _HAS_CUDA, reason="CUDA not available")
def test_nonexistent_device():
    """Non-existent CUDA device returns (False, reason)."""
    ok, reason = check_cuda_available("cuda:99")
    assert not ok
    assert "cuda_device_not_found" in reason
