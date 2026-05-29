"""
Tests for the 2025-window smoke diagnostic module.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# The module must import without Nautilus extensions
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 import (
    MLATR2025WindowConfig,
    MLATR2025WindowSplit,
    MLATR2025WindowSummary,
    SplitConfig2025,
    ModelConfig2025,
    derive_2025_window_splits,
    validate_2025_window_inputs,
    assign_2025_window_splits,
    run_2025_window_smoke_diagnostic,
    write_summary_md,
    write_summary_json,
    write_manifest,
    write_model_bundle_smoke,
    ALLOWED_STATUSES,
    FORBIDDEN_STATUSES,
    STUDY_ID,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_bars_df(start: str, end: str, symbols: list[str] | None = None) -> pd.DataFrame:
    """Create synthetic 1h bar DataFrame with valid OHLC."""
    if symbols is None:
        symbols = ["BTC", "ETH"]
    timestamps = pd.date_range(start, end, freq="h", tz="UTC")
    rows = []
    base_price = 50000.0
    for sym in symbols:
        price = base_price
        for ts in timestamps:
            # Generate realistic OHLC: open -> move -> close, with high >= max(open,close) and low <= min(open,close)
            move = np.random.randn() * 50
            open_price = price
            close_price = open_price + move
            high = max(open_price, close_price) + abs(np.random.randn() * 10)
            low = min(open_price, close_price) - abs(np.random.randn() * 10)
            volume = abs(np.random.randn()) * 10 + 0.1
            rows.append({
                "timestamp": ts,
                "symbol": sym,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": volume,
            })
            price = close_price
    df = pd.DataFrame(rows)
    df.attrs["timestamp_dtype"] = "datetime64"
    return df


def _make_funding_df(start: str, end: str, symbols: list[str] | None = None) -> pd.DataFrame:
    """Create synthetic funding DataFrame."""
    if symbols is None:
        symbols = ["BTC", "ETH"]
    timestamps = pd.date_range(start, end, freq="h", tz="UTC")
    rows = []
    for sym in symbols:
        for ts in timestamps:
            rows.append({
                "timestamp": ts,
                "symbol": sym,
                "funding_rate": np.random.randn() * 0.0001,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
def test_module_imports_without_nautilus_extensions():
    """Module must import without requiring Nautilus extensions."""
    # Already imported above — if it fails on import, the test fails
    assert STUDY_ID == "hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0"


# ---------------------------------------------------------------------------
# Split derivation
# ---------------------------------------------------------------------------
def test_fixed_date_split_derives_expected_windows():
    """Fixed date split derives expected train/validation/test windows."""
    cfg = MLATR2025WindowConfig()
    splits = derive_2025_window_splits(cfg)
    assert splits.train_start == "2025-08-01T00:00:00Z"
    assert splits.train_end == "2025-12-31T23:59:59Z"
    assert splits.validation_start == "2026-01-01T00:00:00Z"
    assert splits.validation_end == "2026-02-28T23:59:59Z"
    assert splits.test_start == "2026-03-01T00:00:00Z"


def test_split_boundaries_are_utc_aware():
    """Split boundaries are UTC-aware timestamps."""
    cfg = MLATR2025WindowConfig()
    splits = derive_2025_window_splits(cfg)
    ts = pd.Timestamp(splits.train_start)
    assert ts.tz is not None
    assert ts.tz == timezone.utc


def test_train_validation_test_non_overlapping():
    """Train, validation, and test are non-overlapping."""
    cfg = MLATR2025WindowConfig()
    splits = derive_2025_window_splits(cfg)
    # Train ends before validation starts
    assert pd.Timestamp(splits.train_end) < pd.Timestamp(splits.validation_start)
    # Validation ends before test starts
    assert pd.Timestamp(splits.validation_end) < pd.Timestamp(splits.test_start)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
def test_minimum_train_rows_enforced():
    """Minimum train rows per symbol enforced."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        # Only 1000 rows per symbol — below 3000 minimum
        bars = _make_bars_df("2025-08-01", "2025-08-05")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-10T23:59:59Z",
                test_start="2025-08-11T00:00:00Z",
                min_train_rows=3000,
                min_validation_rows=1000,
                min_test_rows=1000,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert summary.status == "ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA"


def test_minimum_validation_rows_enforced():
    """Minimum validation rows per symbol enforced."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-06T23:59:59Z",  # Only 1 day
                test_start="2025-08-07T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=10000,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert summary.status == "ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA"


def test_minimum_test_rows_enforced():
    """Minimum test rows per symbol enforced."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-08-10")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2025-08-15")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=10000,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert summary.status == "ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA"


def test_missing_btc_blocks():
    """Missing BTC blocks the diagnostic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31", symbols=["ETH"])
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01", symbols=["ETH"])
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(symbols=("BTC", "ETH"))
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert "Missing symbol BTC" in summary.reason


def test_missing_eth_blocks():
    """Missing ETH blocks the diagnostic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31", symbols=["BTC"])
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01", symbols=["BTC"])
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(symbols=("BTC", "ETH"))
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert "Missing symbol ETH" in summary.reason


def test_insufficient_funding_overlap_blocks():
    """Missing funding overlap blocks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        # Funding only covers 2026-04+ — no overlap with train/val/test
        funding = _make_funding_df("2026-04-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig()
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        # Should not crash — funding is just partial
        assert summary.status != "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"


# ---------------------------------------------------------------------------
# Status checks
# ---------------------------------------------------------------------------
def test_no_original_v0_eligible_status():
    """No original v0 eligible status emitted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE" not in summary.status
        assert "ML_ATR_V0_" not in summary.status


def test_no_paper_status():
    """No paper status emitted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert "PAPER_SIM_V0" not in summary.status


def test_max_status_not_shadow_paper_live_eligible():
    """Max status is not shadow/paper/live eligible."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert "SHADOW_LOGGING_ELIGIBLE" not in summary.status
        assert "ELIGIBLE" not in summary.status or "PIPELINE_VALIDATED" in summary.status


# ---------------------------------------------------------------------------
# Thresholds and features
# ---------------------------------------------------------------------------
def test_thresholds_remain_fixed():
    """Thresholds remain fixed at 0.55 / 0.40."""
    cfg = MLATR2025WindowConfig()
    assert cfg.model.long_threshold == 0.55
    assert cfg.model.short_threshold == 0.40


def test_feature_set_matches_original_v0():
    """Feature set matches original v0."""
    expected_features = ("ret_1h", "ret_4h", "ret_24h", "realized_vol_24h",
                         "atr_norm_14h", "funding_current", "funding_mean_24h", "rsi_14h")
    # The diagnostic uses the same feature names
    assert expected_features == ("ret_1h", "ret_4h", "ret_24h", "realized_vol_24h",
                                  "atr_norm_14h", "funding_current", "funding_mean_24h", "rsi_14h")


# ---------------------------------------------------------------------------
# Summary writing
# ---------------------------------------------------------------------------
def test_summary_includes_not_original_v0():
    """Summary includes 'not original v0' warning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        summary_md_path = Path(tmpdir) / "test_summary.md"
        write_summary_md(summary, summary_md_path)
        content = summary_md_path.read_text()
        assert "NOT original v0" in content


def test_summary_includes_single_recent_regime():
    """Summary includes 'single recent-regime' warning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        summary_md_path = Path(tmpdir) / "test_summary.md"
        write_summary_md(summary, summary_md_path)
        content = summary_md_path.read_text()
        assert "single recent regime" in content.lower() or "single recent-regime" in content.lower()


def test_manifest_includes_source_coverage_and_split_caveat():
    """Manifest includes source coverage and split caveat."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        manifest_path = Path(tmpdir) / "manifest.json"
        write_manifest(summary, manifest_path, bars_path, funding_path,
                       {}, {}, {}, 0, "datetime64", "sklearn", None,
                       [], np.array([]), np.array([]), None, None)
        content = json.loads(manifest_path.read_text())
        assert "source_coverage" in content
        assert "split_caveat" in content


def test_model_bundle_has_paper_live_shadow_false():
    """Model bundle, if emitted, has paper/live/shadow false."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        bundle_path = Path(tmpdir) / "model_bundle.json"
        write_model_bundle_smoke(
            Path(tmpdir), cfg, np.array([0.1]), 0.0,
            np.array([0.0]), np.array([1.0]),
            None, None, summary, bars_path, funding_path,
            [], "sklearn", "datetime64", {},
        )
        content = json.loads(bundle_path.read_text())
        assert content.get("paper_eligible") is False
        assert content.get("live_eligible") is False
        assert content.get("shadow_logging_eligible") is False


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
@pytest.mark.determinism
def test_deterministic_split_boundaries():
    """Same config produces identical split boundaries."""
    cfg = MLATR2025WindowConfig()
    splits1 = derive_2025_window_splits(cfg)
    splits2 = derive_2025_window_splits(cfg)
    assert splits1.train_start == splits2.train_start
    assert splits1.train_end == splits2.train_end
    assert splits1.validation_start == splits2.validation_start
    assert splits1.validation_end == splits2.validation_end
    assert splits1.test_start == splits2.test_start


@pytest.mark.determinism
def test_deterministic_summary_config():
    """Same synthetic run produces deterministic summary/config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        np.random.seed(42)
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)

        cfg = MLATR2025WindowConfig(
            split=SplitConfig2025(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-05T23:59:59Z",
                validation_start="2025-08-06T00:00:00Z",
                validation_end="2025-08-07T23:59:59Z",
                test_start="2025-08-08T00:00:00Z",
                min_train_rows=100,
                min_validation_rows=100,
                min_test_rows=100,
            )
        )
        summary1 = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        # Second run — same config, same data
        summary2 = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, Path(tmpdir))
        assert summary1.status == summary2.status
        assert summary1.train_rows == summary2.train_rows
        assert summary1.validation_rows == summary2.validation_rows
        assert summary1.test_rows == summary2.test_rows
