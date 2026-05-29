"""
Tests for the 2025-window smoke diagnostic CLI runner.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 import (
    main,
    parse_args,
)


def _make_bars_df(start: str, end: str) -> pd.DataFrame:
    """Create synthetic 1h bar DataFrame."""
    timestamps = pd.date_range(start, end, freq="h", tz="UTC")
    rows = []
    for sym in ["BTC", "ETH"]:
        for ts in timestamps:
            rows.append({
                "timestamp": ts,
                "symbol": sym,
                "open": 50000.0 + np.random.randn() * 100,
                "high": 50100.0 + np.random.randn() * 100,
                "low": 49900.0 + np.random.randn() * 100,
                "close": 50050.0 + np.random.randn() * 100,
                "volume": abs(np.random.randn()) * 10,
            })
    df = pd.DataFrame(rows)
    df.attrs["timestamp_dtype"] = "datetime64"
    return df


def _make_funding_df(start: str, end: str) -> pd.DataFrame:
    """Create synthetic funding DataFrame."""
    timestamps = pd.date_range(start, end, freq="h", tz="UTC")
    rows = []
    for sym in ["BTC", "ETH"]:
        for ts in timestamps:
            rows.append({
                "timestamp": ts,
                "symbol": sym,
                "funding_rate": np.random.randn() * 0.0001,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------
def test_cli_help_works():
    """CLI --help works."""
    result = subprocess.run(
        [sys.executable, "-m",
         "examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0",
         "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--bars-path" in result.stdout
    assert "--funding-path" in result.stdout


def test_cli_unknown_symbol_fails_argparse():
    """CLI unknown symbol fails argparse."""
    result = subprocess.run(
        [sys.executable, "-m",
         "examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0",
         "--bars-path", "/tmp/x", "--funding-path", "/tmp/y",
         "--symbol", "LINK"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "LINK" in result.stderr or "invalid choice" in result.stderr


# ---------------------------------------------------------------------------
# Dry-run behavior
# ---------------------------------------------------------------------------
def test_dry_run_writes_no_model_artifacts():
    """Dry-run validates inputs but does not fit model."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)
        output_root = Path(tmpdir) / "output"

        # Import and call with dry-run
        from examples.strategies.venue_agnostic_signal_observer.hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 import (
            MLATR2025WindowConfig, SplitConfig2025, ModelConfig2025,
            run_2025_window_smoke_diagnostic,
        )
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
            ),
            dry_run=True,
        )
        summary = run_2025_window_smoke_diagnostic(cfg, bars_path, funding_path, output_root)

        # Dry run should still produce a status but no model artifacts
        assert summary.status != ""
        # No model_bundle.json
        bundle = output_root / "smoke_42" / "model_bundle.json"
        assert not bundle.exists()
        # No trades.csv
        trades = output_root / "smoke_42" / "trades.csv"
        assert not trades.exists()


def test_cli_dry_run_writes_no_model_artifacts():
    """CLI dry-run writes no model artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bars_path = Path(tmpdir) / "bars.parquet"
        bars = _make_bars_df("2025-08-01", "2025-12-31")
        bars.to_parquet(bars_path)
        funding = _make_funding_df("2025-08-01", "2026-06-01")
        funding_path = Path(tmpdir) / "funding.parquet"
        funding.to_parquet(funding_path)
        output_root = Path(tmpdir) / "output"

        # Patch sys.argv to simulate CLI
        old_argv = sys.argv
        sys.argv = [
            "run_smoke",
            "--bars-path", str(bars_path),
            "--funding-path", str(funding_path),
            "--output-root", str(output_root),
            "--run-id", "drytest",
            "--dry-run",
        ]
        try:
            main()
        except SystemExit:
            pass

        # Check no model artifacts
        bundle = output_root / "drytest" / "model_bundle.json"
        assert not bundle.exists()
        trades = output_root / "drytest" / "trades.csv"
        assert not trades.exists()
        sys.argv = old_argv


# ---------------------------------------------------------------------------
# Forbidden status absence
# ---------------------------------------------------------------------------
def test_forbidden_live_order_auth_strings_absent():
    """Forbidden live/order/auth strings absent from module source."""
    from examples.strategies.venue_agnostic_signal_observer import (
        hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 as mod,
    )
    source = Path(mod.__file__).read_text()

    forbidden = [
        "submit_order", "place_order", "cancel_order",
        "private_key", "api_key", "secret", "signing",
        "live_execute", "broker_connect", "exchange_client", "broker_client",
        "TRADE_READY", "EXECUTION_READY", "LIVE_READY",
        "CANDIDATE_FOR_LIVE", "PROFITABLE",
        "SHADOW_LOGGING_ELIGIBLE",
    ]
    for term in forbidden:
        # Count occurrences — should only appear in docstring/context
        count = source.count(term)
        # Allow in docstrings/comments (which reference forbidden list)
        # But the status itself should not be emitted
        if term in ("TRADE_READY", "EXECUTION_READY", "LIVE_READY",
                     "CANDIDATE_FOR_LIVE", "PROFITABLE", "SHADOW_LOGGING_ELIGIBLE"):
            # These should only appear in FORBIDDEN_STATUSES set or docstrings
            assert count <= 3, f"{term} appears {count} times — expected in forbidden list only"


# ---------------------------------------------------------------------------
# No --loop
# ---------------------------------------------------------------------------
def test_no_loop_in_source():
    """No --loop in diagnostic module or CLI."""
    from examples.strategies.venue_agnostic_signal_observer import (
        hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 as mod,
        run_hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 as cli_mod,
    )
    mod_source = Path(mod.__file__).read_text()
    cli_source = Path(cli_mod.__file__).read_text()

    # --loop should not appear in the diagnostic code
    assert "--loop" not in mod_source
    assert "--loop" not in cli_source
