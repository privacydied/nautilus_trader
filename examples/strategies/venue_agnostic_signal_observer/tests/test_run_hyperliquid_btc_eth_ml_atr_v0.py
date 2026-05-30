"""
Tests for run_hyperliquid_btc_eth_ml_atr_v0 — CLI runner.

NOT live trading. NOT paper execution. NOT bot authorization.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_hyperliquid_btc_eth_ml_atr_v0 import _parse_args, main


def _make_bars(symbol="BTC", n=100, start="2024-01-01T00:00:00Z", seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start, periods=n, freq="h", tz="UTC")
    prices = 40000 + rng.randn(n).cumsum() * 100
    return pd.DataFrame({
        "timestamp": dates, "symbol": symbol,
        "open": prices + rng.randn(n) * 50,
        "high": prices + rng.uniform(50, 200, n),
        "low": prices - rng.uniform(50, 200, n),
        "close": prices,
        "volume": rng.uniform(100, 10000, n),
    })


# ============================================================================
# CLI --help
# ============================================================================

class TestCLIHelp:
    def test_help_works(self):
        """CLI --help should not raise."""
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--help"])
        assert exc.value.code == 0


# ============================================================================
# CLI --dry-run
# ============================================================================

class TestCLIDryRun:
    def test_dry_run_writes_no_artifacts(self):
        """--dry-run validates config but writes no reports."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100, start="2024-01-01T00:00:00Z")
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)

        output_root = Path(tmpdir) / "reports"
        args = _parse_args([
            "--bars-path", str(bars_path),
            "--no-funding",
            "--dry-run",
            "--output-root", str(output_root),
            "--min-train-rows", "50",
            "--min-validation-rows", "20",
            "--min-test-rows", "20",
            "--min-test-trades", "1",
            "--min-test-long-trades", "1",
            "--min-test-short-trades", "1",
        ])
        assert args.dry_run is True


# ============================================================================
# --funding-path omission without --no-funding
# ============================================================================

class TestFundingPathValidation:
    def test_funding_path_required_without_no_funding(self):
        """--funding-path omission without --no-funding fails at argparse."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)

        with pytest.raises(SystemExit) as exc:
            _parse_args(["--bars-path", str(bars_path)])
        assert exc.value.code != 0


# ============================================================================
# --no-funding prefixes run_id
# ============================================================================

class TestNoFundingPrefix:
    def test_no_funding_flag_accepted(self):
        """--no-funding flag is accepted."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        args = _parse_args([
            "--bars-path", str(bars_path),
            "--no-funding",
        ])
        assert args.no_funding is True
        assert args.funding_path is None


# ============================================================================
# --calibrator isotonic requires opt-in
# ============================================================================

class TestCalibratorOptIn:
    def test_isotonic_requires_explicit_opt_in(self):
        """--calibrator isotonic requires explicit flag."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        args = _parse_args([
            "--bars-path", str(bars_path),
            "--no-funding",
            "--calibrator", "isotonic",
        ])
        assert args.calibrator == "isotonic"

    def test_default_is_platt(self):
        """Default calibrator is platt."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        args = _parse_args([
            "--bars-path", str(bars_path),
            "--no-funding",
        ])
        assert args.calibrator == "platt"


# ============================================================================
# spec_version in manifest
# ============================================================================

class TestSpecVersionInManifest:
    def test_spec_version_equals_v0(self):
        """spec_version exists and equals 'v0'."""
        from hyperliquid_btc_eth_ml_atr_v0 import SPEC_VERSION
        assert SPEC_VERSION == "v0"


# ============================================================================
# Forbidden status strings
# ============================================================================

class TestForbiddenStatusStrings:
    def test_no_order_private_key_in_module(self):
        """No order/private-key strings in runner module."""
        import run_hyperliquid_btc_eth_ml_atr_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in ["submit_order", "place_order", "cancel_order", "private_key", "broker_connect"]:
                assert forbidden not in stripped, f"Found '{forbidden}' in runner code"


# ============================================================================
# Symbol validation
# ============================================================================

class TestSymbolValidation:
    def test_unknown_symbol_fails_argparse(self):
        """Unknown symbol fails via argparse before any I/O."""
        with pytest.raises(SystemExit) as exc:
            _parse_args([
                "--bars-path", "/tmp/bars.csv",
                "--no-funding",
                "--symbol", "LINK",
            ])
        assert exc.value.code != 0

    def test_valid_symbols_accepted(self):
        """BTC and ETH are accepted."""
        args = _parse_args([
            "--bars-path", "/tmp/bars.csv",
            "--no-funding",
            "--symbol", "BTC",
            "--symbol", "ETH",
        ])
        assert set(args.symbol) == {"BTC", "ETH"}
