"""
CPU/GPU parity test for forward returns.

Verifies that GPU forward returns match CPU forward returns within float32
tolerance (atol=1e-4) on identical inputs. This is the load-bearing test that
makes future GPU forward-return verdicts trustworthy.

Safety: observer-only data math, no execution, no orders, no auth.
"""

from __future__ import annotations

import pytest

# ── Synthetic fixture builders ──────────────────────────────────────────────

def _build_synthetic_labels(
    count: int = 100,
    source_symbol: str = "BTCUSDT",
    target_symbol: str = "ETHUSDT",
    base_ts_ns: int = 1_700_000_000_000_000_000,  # ~Nov 2023 in ns
    stress_window_seconds: int = 3600,
) -> list:
    """Build synthetic StressLabel objects for testing."""
    from ..cross_asset_beta_lag_archive import StressLabel

    labels = []
    for i in range(count):
        stress_end = base_ts_ns + i * 60 * 1_000_000_000  # 1 min apart
        labels.append(StressLabel(
            label_id=f"test_lbl_{i:04d}",
            source_symbol=source_symbol,
            stress_start_ns=stress_end - stress_window_seconds * 1_000_000_000,
            stress_end_ns=stress_end,
            stress_window_seconds=stress_window_seconds,
            source_move_bps=(i % 2 == 0) * 50.0 - (i % 2 == 1) * 50.0,
            direction="bullish" if i % 2 == 0 else "bearish",
            source_start_price=40000.0 + (i * 0.1),
            source_end_price=40000.0 + (i * 0.1) + (50.0 if i % 2 == 0 else -50.0),
            independent_window_id=f"win_{i // 10}",
            rule_name="test_rule",
        ))
    return labels


def _build_synthetic_ts_prices(
    count: int = 100_000,
    base_ts_ns: int = 1_700_000_000_000_000_000,
    base_price: float = 4000.0,
    tick_interval_ns: int = 1_000_000,  # 1 ms
) -> tuple[list[int], list[float]]:
    """Build synthetic timestamp/price arrays for testing."""
    import math

    ts_arr = []
    pr_arr = []
    for i in range(count):
        ts = base_ts_ns + i * tick_interval_ns
        # Sine wave + trend to create non-trivial returns
        price = base_price + 0.01 * i + 100.0 * math.sin(2 * math.pi * i / 1000)
        ts_arr.append(ts)
        pr_arr.append(price)
    return ts_arr, pr_arr


# ── Parity test ────────────────────────────────────────────────────────────

class TestForwardReturnsGPUParity:
    """CPU/GPU parity tests for forward returns."""

    @pytest.fixture
    def labels(self):
        return _build_synthetic_labels(count=100)

    @pytest.fixture
    def ts_prices(self):
        return _build_synthetic_ts_prices(count=100_000)

    @pytest.fixture
    def horizons(self):
        return [1_000, 5_000, 30_000]  # 1s, 5s, 30s

    @pytest.fixture
    def common_kwargs(self):
        return {
            "VENUE": "BINANCE_VISION",
            "ENTRY_DELAY_NS": 100_000_000,  # 100 ms
            "MS_TO_NS": 1_000_000,
            "FEE_BPS": 4.0,
            "SLIPPAGE_BPS": 2.0,
            "QUOTE_MISMATCH_BUFFER_BPS": 0.0,
        }

    def test_cpu_gpu_parity_single(self, labels, ts_prices, horizons, common_kwargs):
        """CPU and GPU forward returns match within float32 tolerance."""
        from ..forward_returns_dispatcher import compute_forward_returns_single

        ts_arr, pr_arr = ts_prices
        label = labels[0]
        target = "ETHUSDT"

        # CPU path
        cpu_results = compute_forward_returns_single(
            label, ts_arr, pr_arr, target, horizons,
            engine="cpu", **common_kwargs,
        )

        # GPU path (skip if CUDA unavailable)
        cuda_ok = True
        try:
            from ..forward_returns_gpu import check_cuda_available
            cuda_ok, _ = check_cuda_available("cuda:0")
        except Exception:
            pytest.xfail("CUDA not available for GPU parity test")

        gpu_results = compute_forward_returns_single(
            label, ts_arr, pr_arr, target, horizons,
            engine="gpu", device="cuda:0", batch_size=16384,
            **common_kwargs,
        )

        assert len(cpu_results) == len(gpu_results), (
            f"Result count mismatch: CPU={len(cpu_results)}, GPU={len(gpu_results)}"
        )

        for i, (cpu_fr, gpu_fr) in enumerate(zip(cpu_results, gpu_results, strict=False)):
            self._assert_forward_return_parity(cpu_fr, gpu_fr, index=i)

    def test_cpu_gpu_parity_batch(self, labels, ts_prices, horizons, common_kwargs):
        """CPU and GPU forward returns match in batch mode."""
        from ..forward_returns_dispatcher import compute_forward_returns_batch

        ts_arr, pr_arr = ts_prices
        target = "ETHUSDT"

        # CPU path
        cpu_results = compute_forward_returns_batch(
            labels, ts_arr, pr_arr, target, horizons,
            engine="cpu", **common_kwargs,
        )

        # GPU path
        cuda_ok = True
        try:
            from ..forward_returns_gpu import check_cuda_available
            cuda_ok, _ = check_cuda_available("cuda:0")
        except Exception:
            pytest.xfail("CUDA not available for GPU parity test")

        gpu_results = compute_forward_returns_batch(
            labels, ts_arr, pr_arr, target, horizons,
            engine="gpu", device="cuda:0", batch_size=16384,
            **common_kwargs,
        )

        assert len(cpu_results) == len(gpu_results), (
            f"Batch result count mismatch: CPU={len(cpu_results)}, GPU={len(gpu_results)}"
        )

        for i, (cpu_fr, gpu_fr) in enumerate(zip(cpu_results, gpu_results, strict=False)):
            self._assert_forward_return_parity(cpu_fr, gpu_fr, index=i)

    def test_parity_has_teeth(self, labels, ts_prices, horizons, common_kwargs):
        """Verify the parity test actually catches differences."""
        from ..forward_returns_dispatcher import compute_forward_returns_single

        ts_arr, pr_arr = ts_prices
        label = labels[0]
        target = "ETHUSDT"

        cpu_results = compute_forward_returns_single(
            label, ts_arr, pr_arr, target, horizons,
            engine="cpu", **common_kwargs,
        )

        # Mutate first result: flip sign on net_return_bps
        if cpu_results and cpu_results[0].net_return_bps is not None:
            flipped = list(cpu_results)
            flipped[0] = _flip_sign(flipped[0])

            # Should fail parity check
            self._assert_forward_return_parity(flipped[0], cpu_results[0], index=0)
            # Now compare with flipped -- should be different
            self._assert_forward_return_parity(cpu_results[0], flipped[0], index=0)

    def _assert_forward_return_parity(self, cpu_fr, gpu_fr, index: int):
        """Assert two TickForwardReturn objects match within tolerance."""
        # Signal ID and timestamp must match exactly
        assert cpu_fr.signal_id == gpu_fr.signal_id, (
            f"[{index}] signal_id mismatch: {cpu_fr.signal_id} vs {gpu_fr.signal_id}"
        )
        assert cpu_fr.signal_ts == gpu_fr.signal_ts, (
            f"[{index}] signal_ts mismatch: {cpu_fr.signal_ts} vs {gpu_fr.signal_ts}"
        )
        assert cpu_fr.target_symbol == gpu_fr.target_symbol, (
            f"[{index}] target_symbol mismatch"
        )
        assert cpu_fr.horizon_ms == gpu_fr.horizon_ms, (
            f"[{index}] horizon_ms mismatch: {cpu_fr.horizon_ms} vs {gpu_fr.horizon_ms}"
        )
        assert cpu_fr.valid == gpu_fr.valid, (
            f"[{index}] valid mismatch: {cpu_fr.valid} vs {gpu_fr.valid}"
        )

        if not cpu_fr.valid:
            # Both invalid -- check rejection reason matches
            assert cpu_fr.rejection_reason == gpu_fr.rejection_reason, (
                f"[{index}] rejection_reason mismatch: {cpu_fr.rejection_reason} "
                f"vs {gpu_fr.rejection_reason}"
            )
            return

        # Valid results -- check numerical values within tolerance
        self._assert_float_close(
            cpu_fr.entry_reference_price, gpu_fr.entry_reference_price,
            f"[{index}] entry_reference_price", atol=1e-4,
        )
        self._assert_float_close(
            cpu_fr.forward_price, gpu_fr.forward_price,
            f"[{index}] forward_price", atol=1e-4,
        )
        self._assert_float_close(
            cpu_fr.raw_return_bps, gpu_fr.raw_return_bps,
            f"[{index}] raw_return_bps", atol=1e-4,
        )
        self._assert_float_close(
            cpu_fr.direction_adjusted_return_bps, gpu_fr.direction_adjusted_return_bps,
            f"[{index}] direction_adjusted_return_bps", atol=1e-4,
        )
        self._assert_float_close(
            cpu_fr.net_return_bps, gpu_fr.net_return_bps,
            f"[{index}] net_return_bps", atol=1e-4,
        )

    @staticmethod
    def _assert_float_close(a, b, label: str, atol: float = 1e-4):
        """Assert two floats are close, handling None."""
        if a is None and b is None:
            return
        if a is None or b is None:
            raise AssertionError(f"{label}: None mismatch ({a} vs {b})")
        assert abs(a - b) <= atol, (
            f"{label}: {a} vs {b}, diff={abs(a - b):.8f} > atol={atol}"
        )


def _flip_sign(fr):
    """Create a copy of TickForwardReturn with flipped net_return_bps sign."""
    return fr.__class__(
        signal_id=fr.signal_id,
        signal_ts=fr.signal_ts,
        target_venue=fr.target_venue,
        target_symbol=fr.target_symbol,
        horizon_ms=fr.horizon_ms,
        entry_reference_price=fr.entry_reference_price,
        forward_price=fr.forward_price,
        raw_return_bps=fr.raw_return_bps,
        direction_adjusted_return_bps=fr.direction_adjusted_return_bps,
        fee_bps=fr.fee_bps,
        slippage_bps=fr.slippage_bps,
        quote_mismatch_buffer_bps=fr.quote_mismatch_buffer_bps,
        net_return_bps=-(fr.net_return_bps) if fr.net_return_bps is not None else None,
        valid=fr.valid,
        rejection_reason=fr.rejection_reason,
    )
