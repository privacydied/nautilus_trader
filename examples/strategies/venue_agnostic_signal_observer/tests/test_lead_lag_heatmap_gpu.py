"""Tests for lead_lag_heatmap_gpu module.

Coverage:
1. Known synthetic source leads target at known lag → significant correlation
2. No overlap → INSUFFICIENT_OVERLAP
3. Sparse series → INSUFFICIENT_SAMPLES
4. NaN/inf values filtered
5. CPU/GPU parity on synthetic data (if CUDA available)
6. Explicit GPU unavailable diagnostic when CUDA mocked unavailable
7. Chunk-size invariance (GPU batch_size)
8. Output schema stability
9. No forbidden imports (order/trading/auth)
10. Allowed verdict labels only
11. write_heatmap_reports produces all 3 files
12. Runner CLI arg parsing
"""
from __future__ import annotations

import importlib
import inspect
import json
import math
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest

from venue_agnostic_signal_observer.lead_lag_heatmap_gpu import (
    DEFAULT_LAGS_MS,
    _DIAGNOSTIC_READY,
    _GPU_UNAVAILABLE,
    _INSUFFICIENT_OVERLAP,
    _INSUFFICIENT_SAMPLES,
    _NO_SIGNAL_SERIES,
    LeadLagHeatmapRow,
    LeadLagHeatmapSummary,
    _alignment,
    _bucketize_series,
    _finite_pair_series,
    _pearson,
    check_cuda_available,
    compute_lead_lag_heatmap,
    write_heatmap_reports,
)
from venue_agnostic_signal_observer.run_lead_lag_heatmap import build_parser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_NS = 1_700_000_000_000_000_000


def _make_ts(n: int, interval_ns: int = 100_000_000, base: int = _BASE_NS) -> list[int]:
    """Monotonically increasing timestamps."""
    return [base + i * interval_ns for i in range(n)]


def _make_leading_source(n: int, amplitude: float = 10.0) -> list[float]:
    """Create a source series with a cosine impulse."""
    import random
    rng = random.Random(42)
    base_mean = 100.0
    return [base_mean + amplitude * math.sin(2 * math.pi * i / 50) + rng.gauss(0, 0.5) for i in range(n)]


def _make_lagging_target(source: list[float], lag_indices: int, noise_std: float = 0.3) -> list[float]:
    """Target follows source with lag_indices delay + noise."""
    import random
    rng = random.Random(99)
    n = len(source)
    target = [0.0] * n
    for i in range(n):
        src_idx = max(0, i - lag_indices)
        target[i] = source[src_idx] + rng.gauss(0, noise_std)
    return target


# ---------------------------------------------------------------------------
# 1. Known synthetic source leads target at known lag
# ---------------------------------------------------------------------------

class TestKnownLagCorrelation:
    def test_correlation_peaks_at_known_lag(self):
        # With bucket_ms=1 and n=600, we get 600 buckets — plenty for min_samples=20
        n = 600
        source = _make_leading_source(n, amplitude=10.0)
        target = _make_lagging_target(source, 5, noise_std=0.3)
        src_ts = _make_ts(n, interval_ns=1_000_000)  # 1ms ticks
        tgt_ts = _make_ts(n, interval_ns=1_000_000)

        summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=source,
            target_timestamps=tgt_ts,
            target_values=target,
            lags_ms=[5, 10, 50, 100, 500, 1000, 5000],
            bucket_ms=1,  # 1 bucket per value → full resolution
            min_samples=20,
            engine="cpu",
        )
        assert summary.verdict == _DIAGNOSTIC_READY, f"Expected READY, got {summary.verdict}"
        # At lag=5ms (matching the true lag of 5 indices at 1ms ticks), correlation should be strong
        lag_5 = [r for r in summary.rows if r["lag_ms"] == 5]
        assert len(lag_5) == 1
        c5 = lag_5[0]["correlation"]
        assert c5 is not None and abs(c5) > 0.5, f"Expected strong correlation at true lag, got {c5}"


# ---------------------------------------------------------------------------
# 2. No overlap → INSUFFICIENT_OVERLAP
# ---------------------------------------------------------------------------

class TestNoOverlap:
    def test_disjoint_timestamps_returns_insufficient_overlap(self):
        # Source timestamps are way before target — no overlap
        src_ts = [1_000_000, 2_000_000, 3_000_000]
        tgt_ts = [99_000_000_000, 100_000_000_000, 101_000_000_000]
        src_vals = [100.0, 101.0, 102.0]
        tgt_vals = [200.0, 201.0, 202.0]

        summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=src_vals,
            target_timestamps=tgt_ts,
            target_values=tgt_vals,
            lags_ms=[500],
            bucket_ms=1,
            min_samples=2,
            engine="cpu",
        )
        assert summary.verdict == _INSUFFICIENT_OVERLAP


# ---------------------------------------------------------------------------
# 3. Sparse series → INSUFFICIENT_SAMPLES
# ---------------------------------------------------------------------------

class TestSparseSeries:
    def test_too_few_buckets(self):
        # 10 values, bucket_ms default 250, → far fewer than min_samples=500
        src_ts = _make_ts(10)
        tgt_ts = _make_ts(10)
        src_vals = [100.0 + i for i in range(10)]
        tgt_vals = [101.0 + i for i in range(10)]

        summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=src_vals,
            target_timestamps=tgt_ts,
            target_values=tgt_vals,
            lags_ms=[500],
            min_samples=500,  # way more than available
            engine="cpu",
        )
        assert summary.verdict in (_INSUFFICIENT_SAMPLES, _NO_SIGNAL_SERIES)


# ---------------------------------------------------------------------------
# 4. NaN/inf values filtered
# ---------------------------------------------------------------------------

class TestNaNInfFiltered:
    def test_nan_values_are_filtered(self):
        n = 200
        src_ts = _make_ts(n, interval_ns=1_000_000)
        tgt_ts = _make_ts(n, interval_ns=1_000_000)
        src_vals = [100.0 + i * 0.1 if i % 5 != 0 else float("nan") for i in range(n)]
        tgt_vals = [101.0 + i * 0.1 for i in range(n)]

        summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=src_vals,
            target_timestamps=tgt_ts,
            target_values=tgt_vals,
            lags_ms=[500],
            bucket_ms=1,
            min_samples=10,
            engine="cpu",
        )
        assert summary.verdict in (_DIAGNOSTIC_READY, _INSUFFICIENT_SAMPLES)

    def test_inf_values_are_filtered(self):
        n = 200
        src_ts = _make_ts(n, interval_ns=1_000_000)
        tgt_ts = _make_ts(n, interval_ns=1_000_000)
        src_vals = [100.0 + i * 0.1 if i % 7 != 0 else float("inf") for i in range(n)]
        tgt_vals = [101.0 + i * 0.1 for i in range(n)]

        summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=src_vals,
            target_timestamps=tgt_ts,
            target_values=tgt_vals,
            lags_ms=[500],
            bucket_ms=1,
            min_samples=10,
            engine="cpu",
        )
        assert summary.verdict in (_DIAGNOSTIC_READY, _INSUFFICIENT_SAMPLES)

    def test_all_nan_returns_no_signal(self):
        src_ts = _make_ts(10)
        tgt_ts = _make_ts(10)
        src_vals = [float("nan")] * 10
        tgt_vals = [float("nan")] * 10

        summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=src_vals,
            target_timestamps=tgt_ts,
            target_values=tgt_vals,
            lags_ms=[500],
            min_samples=5,
            engine="cpu",
        )
        assert summary.verdict == _NO_SIGNAL_SERIES


# ---------------------------------------------------------------------------
# 5. CPU/GPU parity on synthetic data
# ---------------------------------------------------------------------------

class TestCPUGPUParity:
    def test_gpu_matches_cpu(self):
        ok, _ = check_cuda_available("cuda:0")
        if not ok:
            pytest.skip("CUDA unavailable")

        n = 400
        src_ts = _make_ts(n, interval_ns=1_000_000)
        tgt_ts = _make_ts(n, interval_ns=1_000_000)
        source = _make_leading_source(n, amplitude=5.0)
        target = _make_lagging_target(source, 5, noise_std=0.2)

        cpu_summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=source,
            target_timestamps=tgt_ts,
            target_values=target,
            lags_ms=[100, 250, 500, 1000, 2000],
            bucket_ms=1,
            min_samples=10,
            engine="cpu",
        )

        gpu_summary = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=source,
            target_timestamps=tgt_ts,
            target_values=target,
            lags_ms=[100, 250, 500, 1000, 2000],
            bucket_ms=1,
            min_samples=10,
            engine="gpu",
            device="cuda:0",
        )

        assert cpu_summary.verdict == gpu_summary.verdict
        assert len(cpu_summary.rows) == len(gpu_summary.rows)

        for cpu_r, gpu_r in zip(cpu_summary.rows, gpu_summary.rows):
            assert cpu_r["lag_ms"] == gpu_r["lag_ms"]
            assert cpu_r["source_venue"] == gpu_r["source_venue"]
            # Correlation should agree to a few decimals
            if cpu_r["correlation"] is not None and gpu_r["correlation"] is not None:
                assert math.isclose(cpu_r["correlation"], gpu_r["correlation"], rel_tol=1e-3, abs_tol=0.01), \
                    f"Lag {cpu_r['lag_ms']}ms: cpu_corr={cpu_r['correlation']} gpu_corr={gpu_r['correlation']}"
            if cpu_r["directional_alignment"] is not None and gpu_r["directional_alignment"] is not None:
                assert math.isclose(cpu_r["directional_alignment"], gpu_r["directional_alignment"], rel_tol=1e-3, abs_tol=0.01), \
                    f"Lag {cpu_r['lag_ms']}ms: cpu_align={cpu_r['directional_alignment']} gpu_align={gpu_r['directional_alignment']}"
            assert cpu_r["verdict"] == gpu_r["verdict"]


# ---------------------------------------------------------------------------
# 6. GPU unavailable diagnostic
# ---------------------------------------------------------------------------

class TestGPUUnavailable:
    def test_gpu_unavailable_with_mocked_torch(self):
        """Mock torch.cuda.is_available to return False; verify GPU_UNAVAILABLE_DIAGNOSTIC."""
        src_ts = _make_ts(100)
        tgt_ts = _make_ts(100)
        src_vals = [100.0] * 100
        tgt_vals = [101.0] * 100

        # Need to reload the module with torch.cuda.is_available patched
        import torch
        original_is_available = torch.cuda.is_available
        try:
            torch.cuda.is_available = lambda: False
            # Re-check should return (False, ...)
            ok, reason = check_cuda_available("cuda:0")
            assert ok is False
            assert "unavailable" in reason.lower() or "not_installed" in reason.lower() or reason == "torch_cuda_unavailable"

            # Now call compute with engine="gpu" — it should produce GPU_UNAVAILABLE
            summary = compute_lead_lag_heatmap(
                source_timestamps=src_ts,
                source_values=src_vals,
                target_timestamps=tgt_ts,
                target_values=tgt_vals,
                lags_ms=[500],
                bucket_ms=1,
                min_samples=10,
                engine="gpu",
            )
            assert summary.verdict == _GPU_UNAVAILABLE, f"Expected GPU_UNAVAILABLE, got {summary.verdict}"
        finally:
            torch.cuda.is_available = original_is_available


# ---------------------------------------------------------------------------
# 7. Chunk-size invariance (GPU batch_size)
# ---------------------------------------------------------------------------

class TestChunkSizeInvariance:
    def test_different_batch_sizes_produce_same_results_gpu(self):
        ok, _ = check_cuda_available("cuda:0")
        if not ok:
            pytest.skip("CUDA unavailable")

        n = 400
        src_ts = _make_ts(n, interval_ns=1_000_000)
        tgt_ts = _make_ts(n, interval_ns=1_000_000)
        source = _make_leading_source(n, amplitude=5.0)
        target = _make_lagging_target(source, 5, noise_std=0.2)

        s1 = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=source,
            target_timestamps=tgt_ts,
            target_values=target,
            lags_ms=[500, 1000],
            bucket_ms=1,
            min_samples=10,
            engine="gpu",
            device="cuda:0",
            batch_size=64,
        )
        s2 = compute_lead_lag_heatmap(
            source_timestamps=src_ts,
            source_values=source,
            target_timestamps=tgt_ts,
            target_values=target,
            lags_ms=[500, 1000],
            bucket_ms=1,
            min_samples=10,
            engine="gpu",
            device="cuda:0",
            batch_size=8192,
        )

        assert len(s1.rows) == len(s2.rows)
        for r1, r2 in zip(s1.rows, s2.rows):
            assert r1["lag_ms"] == r2["lag_ms"]
            if r1["correlation"] is not None and r2["correlation"] is not None:
                assert math.isclose(r1["correlation"], r2["correlation"], rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 8. Output schema stability
# ---------------------------------------------------------------------------

class TestOutputSchema:
    def test_row_has_expected_fields(self):
        row = LeadLagHeatmapRow(
            source_venue="binance_perp",
            target_venue="kraken",
            symbol="BTC/USD",
            signal_type="lead_lag",
            lag_ms=500,
            correlation=0.3,
            directional_alignment=0.55,
            sample_count=100,
            overlap_seconds=900.0,
            verdict=_DIAGNOSTIC_READY,
        )
        d = asdict(row)
        assert set(d.keys()) == {
            "source_venue", "target_venue", "symbol", "signal_type",
            "lag_ms", "correlation", "directional_alignment",
            "sample_count", "overlap_seconds", "verdict", "notes",
        }

    def test_summary_has_expected_fields(self):
        summary = LeadLagHeatmapSummary(capture_dir="/tmp/test")
        d = summary.to_dict()
        assert "capture_dir" in d
        assert "rows" in d
        assert "verdict" in d
        assert "engine" in d
        assert "device" in d
        assert "lags_ms" in d
        assert "batch_size" in d
        assert "safety_mode" in d


# ---------------------------------------------------------------------------
# 9. No forbidden imports
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    def test_module_has_no_order_trading_auth_imports(self):
        import venue_agnostic_signal_observer.lead_lag_heatmap_gpu as mod
        source = inspect.getsource(mod)
        forbidden = ["order", "trade", "auth", "secret", "apikey", "exchange", "nautilus"]
        for word in forbidden:
            for line in source.split("\n"):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if word.lower() in stripped.lower() and "import" in stripped.lower():
                    if "venue_agnostic_signal_observer" in stripped:
                        continue
                    # "trade" appears in "TradeTickLite" references, those are fine
                    if word == "trade" and "TradeTick" in stripped:
                        continue
                    # Allow legitimate uses
                    if word == "trade" and "import" not in stripped:
                        continue
                    pytest.fail(f"Potentially forbidden import-like line: {stripped}")

    def test_safety_mode_is_public_data_observer(self):
        from venue_agnostic_signal_observer.lead_lag_heatmap_gpu import SAFETY_MODE
        assert SAFETY_MODE == "public_data_observer_only"


# ---------------------------------------------------------------------------
# 10. Allowed verdict labels only
# ---------------------------------------------------------------------------

class TestAllowedVerdicts:
    def test_forbidden_verdicts_raise(self):
        for bad_verdict in ["REJECTED", "CANDIDATE", "CANDIDATE_FOR_LONGER_OBSERVATION", "NULL_REJECTED"]:
            with pytest.raises(ValueError, match="Forbidden verdict"):
                LeadLagHeatmapRow(
                    source_venue="src", target_venue="tgt", symbol="BTC/USD",
                    signal_type="test", lag_ms=500, correlation=None,
                    directional_alignment=None, sample_count=0,
                    overlap_seconds=0.0, verdict=bad_verdict,
                )

    def test_allowed_verdicts_pass(self):
        for good_verdict in [_DIAGNOSTIC_READY, _INSUFFICIENT_OVERLAP, _INSUFFICIENT_SAMPLES, _GPU_UNAVAILABLE, _NO_SIGNAL_SERIES]:
            row = LeadLagHeatmapRow(
                source_venue="src", target_venue="tgt", symbol="BTC/USD",
                signal_type="test", lag_ms=500, correlation=None,
                directional_alignment=None, sample_count=0,
                overlap_seconds=0.0, verdict=good_verdict,
            )
            assert row.verdict == good_verdict

    def test_summary_forbidden_verdict_raises(self):
        with pytest.raises(ValueError, match="Forbidden verdict"):
            LeadLagHeatmapSummary(capture_dir="/tmp", verdict="REJECTED")

    def test_summary_allowed_verdicts(self):
        for good_verdict in [_DIAGNOSTIC_READY, _INSUFFICIENT_OVERLAP, _INSUFFICIENT_SAMPLES, _GPU_UNAVAILABLE, _NO_SIGNAL_SERIES]:
            s = LeadLagHeatmapSummary(capture_dir="/tmp", verdict=good_verdict)
            assert s.verdict == good_verdict


# ---------------------------------------------------------------------------
# 11. write_heatmap_reports produces all 3 files
# ---------------------------------------------------------------------------

class TestWriteReports:
    def test_produces_json_csv_md(self):
        n = 400
        summary = compute_lead_lag_heatmap(
            source_timestamps=_make_ts(n, interval_ns=1_000_000),
            source_values=_make_leading_source(n, amplitude=5.0),
            target_timestamps=_make_ts(n, interval_ns=1_000_000),
            target_values=_make_lagging_target(_make_leading_source(n, amplitude=5.0), 5, noise_std=0.2),
            lags_ms=[500, 1000],
            bucket_ms=1,
            min_samples=10,
            engine="cpu",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            write_heatmap_reports(summary, tmpdir)
            assert (Path(tmpdir) / "lead_lag_heatmap_summary.json").exists()
            assert (Path(tmpdir) / "lead_lag_heatmap.csv").exists()
            assert (Path(tmpdir) / "lead_lag_heatmap.md").exists()

            # Verify JSON is valid and contains expected fields
            with open(Path(tmpdir) / "lead_lag_heatmap_summary.json") as f:
                data = json.load(f)
            assert data["verdict"] in [_DIAGNOSTIC_READY, _INSUFFICIENT_SAMPLES]
            assert data["engine"] == "cpu"
            assert data["safety_mode"] == "public_data_observer_only"


# ---------------------------------------------------------------------------
# 12. Runner CLI arg parsing
# ---------------------------------------------------------------------------

class TestRunnerCLI:
    def test_default_args(self):
        parser = build_parser()
        args = parser.parse_args(["--capture-dir", "/tmp/cap", "--out", "/tmp/out"])
        assert args.engine == "cpu"
        assert args.device == "cuda:0"
        assert args.batch_size == 8192
        assert args.min_samples == 50
        assert args.bucket_ms == 250

    def test_gpu_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "--capture-dir", "/tmp/cap", "--out", "/tmp/out",
            "--engine", "gpu", "--device", "cuda:1", "--batch-size", "4096",
        ])
        assert args.engine == "gpu"
        assert args.device == "cuda:1"
        assert args.batch_size == 4096

    def test_lags_ms_parsing(self):
        parser = build_parser()
        args = parser.parse_args([
            "--capture-dir", "/tmp/cap", "--out", "/tmp/out",
            "--lags-ms", "100,500,1000",
        ])
        assert args.lags_ms == "100,500,1000"

    def test_engine_choices(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "--capture-dir", "/tmp/cap", "--out", "/tmp/out",
                "--engine", "tpu",
            ])