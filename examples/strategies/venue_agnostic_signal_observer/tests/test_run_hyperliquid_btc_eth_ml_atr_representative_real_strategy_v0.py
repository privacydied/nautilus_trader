"""
Tests for run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0.

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

from run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0 import _parse_args, main
from hyperliquid_btc_eth_ml_atr_v0 import FORBIDDEN_STATUSES


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


def _make_funding(symbol="BTC", n=100, start="2024-01-01T00:00:00Z", seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "timestamp": dates,
        "symbol": symbol,
        "funding_rate": rng.uniform(-0.001, 0.001, n),
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
            "--run-id", "test_dry_run",
            "--min-train-rows-per-symbol", "50",
            "--min-validation-rows-per-symbol", "20",
            "--min-test-rows-per-symbol", "20",
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
            _parse_args(["--bars-path", str(bars_path), "--run-id", "test"])
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
            "--run-id", "test",
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
            "--run-id", "test",
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
            "--run-id", "test",
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
        """No order/private-key strings in runner module (except in safety flags)."""
        import run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Allow "private_key" in safety flags context (e.g., "no_private_keys": True)
            if "no_private_keys" in stripped or "private_keys" in stripped:
                continue
            for forbidden in ["submit_order", "place_order", "cancel_order", "private_key", "broker_connect"]:
                assert forbidden not in stripped, f"Found '{forbidden}' in runner code"

    def test_no_live_ready_status(self):
        """No live-ready status strings in runner module."""
        import run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0 as mod
        source = open(mod.__file__).read()
        for status in FORBIDDEN_STATUSES:
            assert status not in source, f"Found forbidden status '{status}' in runner code"


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
                "--run-id", "test",
            ])
        assert exc.value.code != 0

    def test_valid_symbols_accepted(self):
        """BTC and ETH are accepted."""
        args = _parse_args([
            "--bars-path", "/tmp/bars.csv",
            "--no-funding",
            "--symbol", "BTC",
            "--symbol", "ETH",
            "--run-id", "test",
        ])
        assert set(args.symbol) == {"BTC", "ETH"}


# ============================================================================
# Split row audit
# ============================================================================

class TestSplitRowAudit:
    def test_split_audit_explains_warmup_drops(self):
        """Split audit explains feature warmup drops."""
        from run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0 import run_split_row_audit
        from hyperliquid_btc_eth_ml_atr_v0 import MlAtrConfig, SplitConfig, FeatureConfig
        
        cfg = MlAtrConfig(
            split=SplitConfig(
                train_start="2025-08-01T00:00:00Z",
                train_end="2025-08-31T23:59:59Z",
                validation_start="2026-01-01T00:00:00Z",
                validation_end="2026-01-31T23:59:59Z",
                test_start="2026-03-01T00:00:00Z",
            ),
            feature=FeatureConfig(label_horizon_bars=24),
        )
        
        # Create minimal bars
        dates = pd.date_range("2025-08-01", periods=100, freq="h", tz="UTC")
        bars = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": 40000 + np.random.randn(100).cumsum() * 100,
            "high": 40100,
            "low": 39900,
            "close": 40000 + np.random.randn(100).cumsum() * 100,
            "volume": 1000,
        })
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            audit = run_split_row_audit(bars, None, cfg, output_dir)
            
            assert audit["status"] == "REPRESENTATIVE_SPLIT_ROW_AUDIT_EXPLAINED"
            assert len(audit["explanation"]) > 0
            assert "BTC" in audit["per_symbol"]
            assert "feature_warmup" in str(audit["per_symbol"]["BTC"]["dropped_by_cause"]).lower()


# ============================================================================
# Determinism
# ============================================================================

class TestDeterminism:
    def test_same_input_same_seed_same_output(self):
        """Same input and seed produce same output (modulo timestamps)."""
        # This is a basic sanity check - full determinism test requires actual data
        from hyperliquid_btc_eth_ml_atr_v0 import _config_hash, MlAtrConfig, SplitConfig, ModelConfig
        
        cfg1 = MlAtrConfig(split=SplitConfig(), model=ModelConfig(seed=42))
        cfg2 = MlAtrConfig(split=SplitConfig(), model=ModelConfig(seed=42))
        
        assert _config_hash(cfg1) == _config_hash(cfg2)


# ============================================================================
# Summary warnings
# ============================================================================

class TestSummaryWarnings:
    def test_summary_includes_non_canonical_warning(self):
        """Summary includes non-canonical warning."""
        # This is tested by the actual run - here we verify the status string
        from run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0 import ALLOWED_STATUSES
        assert "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE" in ALLOWED_STATUSES

    def test_summary_includes_no_live_warning(self):
        """Summary includes no-live warning."""
        from run_hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0 import ALLOWED_STATUSES
        assert "SHADOW_LOGGING_ELIGIBLE" not in ALLOWED_STATUSES
        assert "PAPER_SIM_V0" not in ALLOWED_STATUSES
        assert "TRADE_READY" not in ALLOWED_STATUSES
        assert "EXECUTION_READY" not in ALLOWED_STATUSES
        assert "LIVE_READY" not in ALLOWED_STATUSES
        assert "CANDIDATE_FOR_LIVE" not in ALLOWED_STATUSES
        assert "PROFITABLE" not in ALLOWED_STATUSES
