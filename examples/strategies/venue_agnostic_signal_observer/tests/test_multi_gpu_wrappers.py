"""Tests for the opt-in multi-GPU wrappers across the three GPU modules.

These tests do NOT require real CUDA. The single-device kernel each wrapper
delegates to is monkeypatched with a fake that returns deterministic
shaped output. This lets us assert sharding, gathering, ordering, and
seed-derivation behaviour without GPU hardware.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer import (
    forward_returns_gpu,
    permutation_null_gpu,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import (
    TickSignalEvent,
    TradeTickLite,
)


def _mk_signal(i: int) -> TickSignalEvent:
    return TickSignalEvent(
        signal_id=f"sig_{i}",
        ts_event=1_000_000_000 * (i + 1),
        source_venue="okx",
        source_symbol="BTC/USDT",
        target_venue="bybit",
        target_symbol="BTC/USDT",
        asset="BTC",
        signal_type="tick_lead_lag",
        direction="long",
        lookback_ms=1000,
        threshold_bps=10.0,
        source_move_bps=15.0,
        source_start_price=100.0,
        source_end_price=100.15,
        strength=15.0,
    )


def _mk_target_tick(i: int) -> TradeTickLite:
    return TradeTickLite(
        ts_event=1_000_000_000 * (i + 1),
        venue="bybit",
        symbol="BTC/USDT",
        price=100.0 + i,
        size=0.1,
        side="buy",
    )


# ---------------------------------------------------------------------------
# forward_returns_gpu.batch_evaluate_signals_multi_gpu
# ---------------------------------------------------------------------------


class TestForwardReturnsMultiGpu:
    def test_empty_inputs(self):
        result = forward_returns_gpu.batch_evaluate_signals_multi_gpu(
            signals=[],
            target_ticks=[_mk_target_tick(0)],
            horizons_ms=[100],
            fee_bps=1.0,
            slippage_bps=1.0,
            devices=["cuda:0"],
        )
        assert result == []

    def test_no_devices_raises(self):
        with pytest.raises(ValueError, match="at least one device"):
            forward_returns_gpu.batch_evaluate_signals_multi_gpu(
                signals=[_mk_signal(0)],
                target_ticks=[_mk_target_tick(0)],
                horizons_ms=[100],
                fee_bps=1.0,
                slippage_bps=1.0,
                devices=[],
            )

    def test_single_device_delegates_unchanged(self, monkeypatch):
        # Replace the single-device kernel with a deterministic fake.
        calls = []

        def fake(signals, target_ticks, horizons_ms, fee_bps, slippage_bps,
                 quote_mismatch_buffer_bps, quote_mismatch, chunk_size, device):
            calls.append({"n": len(signals), "device": device})
            return [f"r_{s.signal_id}_{h}" for s in signals for h in horizons_ms]  # type: ignore[misc]

        monkeypatch.setattr(forward_returns_gpu, "batch_evaluate_signals_gpu", fake)
        signals = [_mk_signal(i) for i in range(5)]
        out = forward_returns_gpu.batch_evaluate_signals_multi_gpu(
            signals=signals,
            target_ticks=[_mk_target_tick(0)],
            horizons_ms=[100, 200],
            fee_bps=1.0, slippage_bps=1.0,
            devices=["cuda:0"],
        )
        assert len(calls) == 1
        assert calls[0]["device"] == "cuda:0"
        assert calls[0]["n"] == 5
        assert len(out) == 5 * 2

    def test_multi_device_shards_and_preserves_order(self, monkeypatch):
        calls = []

        def fake(signals, target_ticks, horizons_ms, fee_bps, slippage_bps,
                 quote_mismatch_buffer_bps, quote_mismatch, chunk_size, device):
            calls.append({"n": len(signals), "device": device,
                          "ids": [s.signal_id for s in signals]})
            return [f"r_{s.signal_id}_{h}" for s in signals for h in horizons_ms]  # type: ignore[misc]

        monkeypatch.setattr(forward_returns_gpu, "batch_evaluate_signals_gpu", fake)
        signals = [_mk_signal(i) for i in range(10)]
        out = forward_returns_gpu.batch_evaluate_signals_multi_gpu(
            signals=signals,
            target_ticks=[_mk_target_tick(0)],
            horizons_ms=[100, 200],
            fee_bps=1.0, slippage_bps=1.0,
            devices=["cuda:0", "cuda:1"],
        )
        # Two shards of 5 each.
        assert len(calls) == 2
        assert [c["n"] for c in calls] == [5, 5]
        assert [c["device"] for c in calls] == ["cuda:0", "cuda:1"]
        # Original signal order preserved across the concatenation.
        assert calls[0]["ids"] == [f"sig_{i}" for i in range(5)]
        assert calls[1]["ids"] == [f"sig_{i}" for i in range(5, 10)]
        # 10 signals × 2 horizons.
        assert len(out) == 20
        # Concatenated in original event-major order.
        assert out[0] == "r_sig_0_100"
        assert out[1] == "r_sig_0_200"
        assert out[10] == "r_sig_5_100"

    def test_more_devices_than_signals(self, monkeypatch):
        calls = []

        def fake(signals, target_ticks, horizons_ms, **kw):
            calls.append(len(signals))
            return [None] * (len(signals) * len(horizons_ms))

        monkeypatch.setattr(forward_returns_gpu, "batch_evaluate_signals_gpu", fake)
        out = forward_returns_gpu.batch_evaluate_signals_multi_gpu(
            signals=[_mk_signal(0), _mk_signal(1)],
            target_ticks=[_mk_target_tick(0)],
            horizons_ms=[100],
            fee_bps=1.0, slippage_bps=1.0,
            devices=["cuda:0", "cuda:1", "cuda:2", "cuda:3"],
        )
        # Empty shards are skipped — only two devices were called.
        assert calls == [1, 1]
        assert len(out) == 2


# ---------------------------------------------------------------------------
# permutation_null_gpu.compute_null_distribution_multi_gpu
# ---------------------------------------------------------------------------


class TestPermutationNullMultiGpu:
    def _fake_kernel(self, calls_list):
        def fake(*, source_event_timestamps, target_timestamps, target_prices,
                direction, horizons_ms, fee_bps, slippage_bps,
                quote_mismatch_buffer_bps, quote_mismatch, iterations, seed,
                shift_mode, chunk_size, device):
            calls_list.append({"iterations": iterations, "seed": seed,
                               "device": device})
            n = iterations * len(horizons_ms)
            # Use seed to tag values so we can verify ordering.
            return {
                "null_mean_net_bps": [float(seed % 1000) + i for i in range(n)],
                "null_median_net_bps": [float(seed % 1000) for _ in range(n)],
                "null_win_rates": [0.5 for _ in range(n)],
                "percentiles": {},
                "iterations": iterations, "seed": seed,
                "shift_mode": shift_mode, "engine": "gpu",
                "device": device, "chunk_size": chunk_size,
                "safety_mode": "public_data_observer_only",
            }
        return fake

    def test_no_devices_raises(self):
        with pytest.raises(ValueError, match="at least one device"):
            permutation_null_gpu.compute_null_distribution_multi_gpu(
                source_event_timestamps=[1, 2],
                target_timestamps=[1, 2], target_prices=[100.0, 101.0],
                direction="long", horizons_ms=[100],
                fee_bps=1.0, slippage_bps=1.0,
                iterations=10, seed=42, devices=[],
            )

    def test_block_shift_rejected(self):
        with pytest.raises(ValueError, match="circular_time_shift"):
            permutation_null_gpu.compute_null_distribution_multi_gpu(
                source_event_timestamps=[1, 2],
                target_timestamps=[1, 2], target_prices=[100.0, 101.0],
                direction="long", horizons_ms=[100],
                fee_bps=1.0, slippage_bps=1.0,
                iterations=10, seed=42, shift_mode="block_time_shift",
                devices=["cuda:0"],
            )

    def test_iteration_sharding_sums_to_total(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            permutation_null_gpu, "compute_null_distribution_gpu",
            self._fake_kernel(calls),
        )
        out = permutation_null_gpu.compute_null_distribution_multi_gpu(
            source_event_timestamps=[1, 2, 3],
            target_timestamps=[1, 2, 3], target_prices=[100.0, 101.0, 102.0],
            direction="long", horizons_ms=[100],
            fee_bps=1.0, slippage_bps=1.0,
            iterations=1000, seed=42,
            devices=["cuda:0", "cuda:1", "cuda:2"],
        )
        # 1000 split across 3 devices: 334 + 333 + 333
        assert [c["iterations"] for c in calls] == [334, 333, 333]
        assert out["per_device_iterations"] == [334, 333, 333]
        assert sum(out["per_device_iterations"]) == 1000
        assert out["iterations"] == 1000
        assert out["num_devices"] == 3
        assert out["devices"] == ["cuda:0", "cuda:1", "cuda:2"]
        # Concatenated in device order.
        assert len(out["null_mean_net_bps"]) == 1000

    def test_deterministic_seeds_stable_across_runs(self, monkeypatch):
        calls1, calls2 = [], []
        monkeypatch.setattr(
            permutation_null_gpu, "compute_null_distribution_gpu",
            self._fake_kernel(calls1),
        )
        permutation_null_gpu.compute_null_distribution_multi_gpu(
            source_event_timestamps=[1, 2],
            target_timestamps=[1, 2], target_prices=[100.0, 101.0],
            direction="long", horizons_ms=[100],
            fee_bps=1.0, slippage_bps=1.0,
            iterations=10, seed=42, devices=["cuda:0", "cuda:1"],
        )
        monkeypatch.setattr(
            permutation_null_gpu, "compute_null_distribution_gpu",
            self._fake_kernel(calls2),
        )
        permutation_null_gpu.compute_null_distribution_multi_gpu(
            source_event_timestamps=[1, 2],
            target_timestamps=[1, 2], target_prices=[100.0, 101.0],
            direction="long", horizons_ms=[100],
            fee_bps=1.0, slippage_bps=1.0,
            iterations=10, seed=42, devices=["cuda:0", "cuda:1"],
        )
        # Same seed → same per-device seed derivation.
        assert [c["seed"] for c in calls1] == [c["seed"] for c in calls2]

    def test_seeds_differ_per_device(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            permutation_null_gpu, "compute_null_distribution_gpu",
            self._fake_kernel(calls),
        )
        permutation_null_gpu.compute_null_distribution_multi_gpu(
            source_event_timestamps=[1, 2],
            target_timestamps=[1, 2], target_prices=[100.0, 101.0],
            direction="long", horizons_ms=[100],
            fee_bps=1.0, slippage_bps=1.0,
            iterations=10, seed=42, devices=["cuda:0", "cuda:1", "cuda:2"],
        )
        seeds = [c["seed"] for c in calls]
        assert len(set(seeds)) == 3, f"per-device seeds must be distinct: {seeds}"

    def test_empty_shard_skipped(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            permutation_null_gpu, "compute_null_distribution_gpu",
            self._fake_kernel(calls),
        )
        out = permutation_null_gpu.compute_null_distribution_multi_gpu(
            source_event_timestamps=[1, 2],
            target_timestamps=[1, 2], target_prices=[100.0, 101.0],
            direction="long", horizons_ms=[100],
            fee_bps=1.0, slippage_bps=1.0,
            iterations=2, seed=42,
            devices=["cuda:0", "cuda:1", "cuda:2", "cuda:3"],
        )
        # 2 iters across 4 devices: 1, 1, 0, 0
        assert out["per_device_iterations"] == [1, 1, 0, 0]
        # Only two devices were actually invoked.
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# Safety scan — multi-GPU wrappers introduced no auth/order strings
# ---------------------------------------------------------------------------


def test_no_auth_strings_in_multi_gpu_paths():
    pkg = Path(__file__).parent.parent
    forbidden = [
        "api" + "_key", "API" + "_KEY",
        "secret" + "_key", "private" + "_key",
        "place" + "_order", "submit" + "_order", "create" + "_order",
        "pass" + "phrase", "Authoriz" + "ation",
    ]
    for fname in ("gpu_devices.py", "forward_returns_gpu.py",
                  "permutation_null_gpu.py", "lead_lag_heatmap_gpu.py"):
        text = (pkg / fname).read_text()
        found = [p for p in forbidden if re.search(rf"\b{re.escape(p)}\b", text)]
        assert not found, f"{fname}: forbidden strings present: {found}"
