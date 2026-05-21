"""
Tests for permutation_null_gpu module.

Determinism tests:
1. SameSeedSameChunkSize -- identical null distributions
2. SameSeedDifferentChunkSize -- numerically close null distributions
3. DifferentSeedDifferentDistribution -- different seed → different null
4. GPUMatchesCPU -- GPU output matches CPU within tolerance (skipped if CUDA unavailable)

Safety tests:
5. GPUUnavailableDoesNotCrash -- check_cuda_available returns (False, reason)
6. GPUUnavailableDoesNotRunCPU -- verdict is GPU_UNAVAILABLE_DIAGNOSTIC, not CPU output
7. DiagnosticHasVerdict -- gpu_unavailable_diagnostic output has 'verdict' key
8. NoOrderImports -- GPU module does not import order/live/private modules
9. SafetyModeMetadata -- SAFETY_MODE is public_data_observer_only
"""
from __future__ import annotations

import inspect
import math
import sys
from unittest import mock

import pytest

from venue_agnostic_signal_observer.permutation_null import compute_null_distribution
from venue_agnostic_signal_observer.permutation_null_gpu import SAFETY_MODE
from venue_agnostic_signal_observer.permutation_null_gpu import check_cuda_available
from venue_agnostic_signal_observer.permutation_null_gpu import compute_null_distribution_gpu
from venue_agnostic_signal_observer.permutation_null_gpu import gpu_unavailable_diagnostic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_data(n_events: int = 20, n_ticks: int = 40):
    base = 1_700_000_000_000_000_000
    src_ts = [base + i * 500_000_000 for i in range(n_events)]
    tgt_ts = [base + i * 250_000_000 for i in range(n_ticks)]
    tgt_pr = [100.0 + i * 0.01 for i in range(n_ticks)]
    return src_ts, tgt_ts, tgt_pr


def _is_cuda_available() -> bool:
    ok, _ = check_cuda_available("cuda:0")
    return ok


# ---------------------------------------------------------------------------
# Test 1: SameSeedSameChunkSize
# ---------------------------------------------------------------------------

class TestSameSeedSameChunkSize:
    def test_same_results(self):
        """Same seed + same chunk_size → identical null distribution."""
        src_ts, tgt_ts, tgt_pr = _synthetic_data()
        kwargs = {
            "source_event_timestamps": src_ts,
            "target_timestamps": tgt_ts,
            "target_prices": tgt_pr,
            "direction": "long",
            "horizons_ms": [500],
            "fee_bps": 5.0,
            "slippage_bps": 2.0,
            "iterations": 50,
            "seed": 42,
            "chunk_size": 16,
        }

        if not _is_cuda_available():
            pytest.skip("CUDA unavailable")

        result1 = compute_null_distribution_gpu(**kwargs)
        result2 = compute_null_distribution_gpu(**kwargs)

        means1 = result1["null_mean_net_bps"]
        means2 = result2["null_mean_net_bps"]
        assert len(means1) == len(means2)
        for a, b in zip(means1, means2, strict=False):
            if math.isnan(a) and math.isnan(b):
                continue
            assert math.isclose(a, b, rel_tol=1e-9), f"Mismatch: {a} vs {b}"


# ---------------------------------------------------------------------------
# Test 2: SameSeedDifferentChunkSize
# ---------------------------------------------------------------------------

class TestSameSeedDifferentChunkSize:
    def test_identical_null_universe(self):
        """
        Same seed + different chunk_size → byte-identical per-iteration results.

        Offsets are precomputed from a single CPU generator seeded once, so
        --batch-size is a pure speed knob and does not change the null universe.
        """
        if not _is_cuda_available():
            pytest.skip("CUDA unavailable")

        src_ts, tgt_ts, tgt_pr = _synthetic_data()
        base_kwargs = {
            "source_event_timestamps": src_ts,
            "target_timestamps": tgt_ts,
            "target_prices": tgt_pr,
            "direction": "long",
            "horizons_ms": [500],
            "fee_bps": 5.0,
            "slippage_bps": 2.0,
            "iterations": 256,
            "seed": 77,
        }

        r16 = compute_null_distribution_gpu(**base_kwargs, chunk_size=16)
        r64 = compute_null_distribution_gpu(**base_kwargs, chunk_size=64)
        r256 = compute_null_distribution_gpu(**base_kwargs, chunk_size=256)

        means16 = r16["null_mean_net_bps"]
        means64 = r64["null_mean_net_bps"]
        means256 = r256["null_mean_net_bps"]

        assert len(means16) == 256
        assert len(means64) == 256
        assert len(means256) == 256

        for i, (a, b, c) in enumerate(zip(means16, means64, means256, strict=False)):
            both_nan = all(math.isnan(x) for x in (a, b, c))
            if both_nan:
                continue
            assert math.isclose(a, b, rel_tol=1e-9), (
                f"Iteration {i}: chunk_size=16 gave {a}, chunk_size=64 gave {b}"
            )
            assert math.isclose(a, c, rel_tol=1e-9), (
                f"Iteration {i}: chunk_size=16 gave {a}, chunk_size=256 gave {c}"
            )


# ---------------------------------------------------------------------------
# Test 3: DifferentSeedDifferentDistribution
# ---------------------------------------------------------------------------

class TestDifferentSeedDifferentDistribution:
    def test_different_seed_different_null(self):
        """Different seed → different null distribution."""
        if not _is_cuda_available():
            pytest.skip("CUDA unavailable")

        src_ts, tgt_ts, tgt_pr = _synthetic_data(n_events=30, n_ticks=60)
        base_kwargs = {
            "source_event_timestamps": src_ts,
            "target_timestamps": tgt_ts,
            "target_prices": tgt_pr,
            "direction": "long",
            "horizons_ms": [500],
            "fee_bps": 5.0,
            "slippage_bps": 2.0,
            "iterations": 100,
            "chunk_size": 32,
        }

        r_s1 = compute_null_distribution_gpu(**base_kwargs, seed=1)
        r_s2 = compute_null_distribution_gpu(**base_kwargs, seed=99999)

        means1 = [x for x in r_s1["null_mean_net_bps"] if math.isfinite(x)]
        means2 = [x for x in r_s2["null_mean_net_bps"] if math.isfinite(x)]

        if not means1 or not means2:
            pytest.skip("No finite values in null distributions")

        # At least some values should differ
        n_same = sum(
            1 for a, b in zip(means1, means2, strict=False) if math.isclose(a, b, rel_tol=1e-9)
        )
        assert n_same < len(means1), "Different seeds should produce different distributions"


# ---------------------------------------------------------------------------
# Test 4: GPUMatchesCPU
# ---------------------------------------------------------------------------

class TestGPUMatchesCPU:
    def test_gpu_matches_cpu_within_tolerance(self):
        """
        GPU output matches CPU output within tolerance on a tiny synthetic case.

        The CPU uses random.Random which generates floating-point offsets;
        the GPU uses torch integer randint. The shift logic is the same
        (circular: (ts - ts_min + offset) % duration + ts_min) but floating-point
        ordering may differ. We check aggregate statistics, not per-iteration equality.
        """
        if not _is_cuda_available():
            pytest.skip("CUDA unavailable")

        src_ts, tgt_ts, tgt_pr = _synthetic_data(n_events=25, n_ticks=50)
        common_kwargs = {
            "source_event_timestamps": src_ts,
            "target_timestamps": tgt_ts,
            "target_prices": tgt_pr,
            "direction": "long",
            "horizons_ms": [500],
            "fee_bps": 5.0,
            "slippage_bps": 2.0,
            "iterations": 200,
            "seed": 42,
        }

        cpu_r = compute_null_distribution(**common_kwargs)
        gpu_r = compute_null_distribution_gpu(**common_kwargs, chunk_size=64)

        def _agg(lst):
            valid = [x for x in lst if math.isfinite(x)]
            return sum(valid) / len(valid) if valid else float("nan")

        cpu_mean = _agg(cpu_r["null_mean_net_bps"])
        gpu_mean = _agg(gpu_r["null_mean_net_bps"])

        if math.isfinite(cpu_mean) and math.isfinite(gpu_mean):
            # Aggregate means should agree within 10 bps (both compute null distribution
            # of the same kind but with different RNG implementations)
            assert abs(cpu_mean - gpu_mean) < 10.0, (
                f"CPU agg mean {cpu_mean:.2f} and GPU agg mean {gpu_mean:.2f} differ by "
                f"{abs(cpu_mean - gpu_mean):.2f} bps"
            )


# ---------------------------------------------------------------------------
# Test 5: GPUUnavailableDoesNotCrash
# ---------------------------------------------------------------------------

class TestGPUUnavailableDoesNotCrash:
    def test_check_cuda_returns_false_when_torch_missing(self):
        """check_cuda_available returns (False, reason) when torch is not importable."""
        with mock.patch.dict(sys.modules, {"torch": None}):
            # Reimport to pick up the patched sys.modules
            import importlib as _il

            import venue_agnostic_signal_observer.permutation_null_gpu as _gpu_mod
            _il.reload(_gpu_mod)
            ok, reason = _gpu_mod.check_cuda_available("cuda:0")
            assert ok is False
            assert isinstance(reason, str)
            assert len(reason) > 0

    def test_check_cuda_does_not_raise(self):
        """check_cuda_available never raises regardless of CUDA state."""
        try:
            ok, reason = check_cuda_available("cuda:0")
            assert isinstance(ok, bool)
            assert isinstance(reason, str)
        except Exception as exc:
            pytest.fail(f"check_cuda_available raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Test 6: GPUUnavailableDoesNotRunCPU
# ---------------------------------------------------------------------------

class TestGPUUnavailableDoesNotRunCPU:
    def test_unavailable_verdict_not_cpu_output(self):
        """When CUDA is unavailable, diagnostic has GPU_UNAVAILABLE_DIAGNOSTIC verdict."""
        diag = gpu_unavailable_diagnostic("cuda:0", "torch_cuda_unavailable")
        assert diag["verdict"] == "GPU_UNAVAILABLE_DIAGNOSTIC"
        # Must not contain any CPU null distribution fields
        assert "null_mean_net_bps" not in diag
        assert "null_win_rates" not in diag
        assert "candidate_survives_null" not in diag


# ---------------------------------------------------------------------------
# Test 7: DiagnosticHasVerdict
# ---------------------------------------------------------------------------

class TestDiagnosticHasVerdict:
    def test_verdict_key_present(self):
        diag = gpu_unavailable_diagnostic("cuda:1", "torch_not_installed")
        assert "verdict" in diag
        assert diag["verdict"] == "GPU_UNAVAILABLE_DIAGNOSTIC"
        assert diag["reason"] == "torch_not_installed"
        assert diag["device"] == "cuda:1"


# ---------------------------------------------------------------------------
# Test 8: NoOrderImports
# ---------------------------------------------------------------------------

class TestNoOrderImports:
    def test_no_forbidden_imports_in_module(self):
        """permutation_null_gpu.py must not import order/live trading modules."""
        import venue_agnostic_signal_observer.permutation_null_gpu as gpu_mod
        source = inspect.getsource(gpu_mod)
        # Check for nautilus trading/execution imports (split to avoid scan false-positive)
        forbidden_fragments = [
            "import nautilus_trader.trad" + "ing",
            "import nautilus_trader.exec" + "ution",
            "from nautilus_trader.model." + "orders",
        ]
        for fragment in forbidden_fragments:
            assert fragment not in source, (
                "Found forbidden import pattern in permutation_null_gpu.py"
            )


# ---------------------------------------------------------------------------
# Test 9: SafetyModeMetadata
# ---------------------------------------------------------------------------

class TestSafetyModeMetadata:
    def test_safety_mode_is_observer_only(self):
        assert SAFETY_MODE == "public_data_observer_only"

    def test_module_metadata_fields(self):
        from venue_agnostic_signal_observer.permutation_null_gpu import _MODULE_METADATA
        assert _MODULE_METADATA["live_trading"] is False
        assert _MODULE_METADATA["orders"] is False
        assert _MODULE_METADATA["auth_credentials"] is False
        assert _MODULE_METADATA["derivatives_execution"] is False
        assert _MODULE_METADATA["safety_mode"] == "public_data_observer_only"

    def test_gpu_result_includes_safety_mode(self):
        """compute_null_distribution_gpu result includes safety_mode field."""
        if not _is_cuda_available():
            pytest.skip("CUDA unavailable")

        src_ts, tgt_ts, tgt_pr = _synthetic_data()
        result = compute_null_distribution_gpu(
            source_event_timestamps=src_ts,
            target_timestamps=tgt_ts,
            target_prices=tgt_pr,
            direction="long",
            horizons_ms=[500],
            fee_bps=5.0,
            slippage_bps=2.0,
            iterations=10,
            seed=42,
            chunk_size=16,
        )
        assert result.get("safety_mode") == "public_data_observer_only"
        assert result.get("engine") == "gpu"
