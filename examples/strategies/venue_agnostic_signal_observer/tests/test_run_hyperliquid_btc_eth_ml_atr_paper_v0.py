"""
Tests for run_hyperliquid_btc_eth_ml_atr_paper_v0 — CLI runner.

NOT live trading. NOT paper broker execution. NOT bot authorization.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_hyperliquid_btc_eth_ml_atr_paper_v0 import _parse_args


def _make_bars(symbol="BTC", n=100, start="2025-07-01T00:00:00Z", seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start, periods=n, freq="h", tz="UTC")
    prices = 40000 + rng.randn(n).cumsum() * 100
    return pd.DataFrame({
        "timestamp": dates, "symbol": symbol,
        "open": prices, "high": prices + 100, "low": prices - 100,
        "close": prices, "volume": rng.uniform(100, 10000, n),
    })


def _write_bundle(tmpdir):
    import hashlib
    bundle = {
        "spec_version": "v0", "study_id": "hyperliquid_btc_eth_ml_atr_v0",
        "created_at_utc": "2025-01-01T00:00:00Z", "source_run_id": "test",
        "source_summary_sha256": "", "source_config_sha256": "",
        "source_precommitment_path": "", "source_precommitment_sha256": "",
        "source_summary_status": "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE",
        "source_test_split_status": "completed",
        "source_test_split_boundary_timestamps": {
            "train_start": None, "train_end": "2024-12-31T23:59:59Z",
            "validation_start": "2025-01-01T00:00:00Z", "validation_end": "2025-06-30T23:59:59Z",
            "test_start": "2025-07-01T00:00:00Z", "test_end": None,
        },
        "symbols": ["BTC", "ETH"],
        "feature_names": ["ret_1h", "ret_4h", "ret_24h", "realized_vol_24h",
                          "atr_norm_14h", "funding_current", "funding_mean_24h", "rsi_14h"],
        "scaler_mean": [0.0]*8, "scaler_scale": [1.0]*8,
        "model_backend": "sklearn", "logistic_intercept": 0.0,
        "logistic_coefficients": [0.1]*8, "regularization_C": 1.0,
        "calibrator": "platt", "platt_params": {"a": 0.0, "b": 0.0},
        "thresholds": {"long_threshold": 0.55, "short_threshold": 0.40},
        "feature_config": {"label_horizon_bars": 24, "atr_lookback": 14},
        "exit_config": {"stop_atr_mult": 2.0, "trailing_atr_mult": 3.0},
        "cost_config": {"fee_bps_per_side": 1.0, "slippage_bps_per_side": 0.5,
                        "funding_interval_hours": 1, "max_abs_funding_rate": 0.01,
                        "allow_zero_volume_bars": True},
        "label_horizon_bars": 24,
        "train_window": {"start": None, "end": "2024-12-31T23:59:59Z"},
        "validation_window": {"start": "2025-01-01T00:00:00Z", "end": "2025-06-30T23:59:59Z"},
        "test_window": {"start": "2025-07-01T00:00:00Z", "end": None},
        "latest_training_input_timestamp_by_symbol": {},
        "eligibility_status_from_source_summary": "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE",
        "package_versions": {"python_version": sys.version, "numpy_version": np.__version__,
                             "pandas_version": pd.__version__, "sklearn_version": "1.8.0"},
        "bundle_sha256_self": None,
        "safety": {"observer_only": True, "no_orders": True, "no_auth": True, "no_live_execution": True},
    }
    bundle_for_hash = {k: v for k, v in bundle.items() if k != "bundle_sha256_self"}
    raw = json.dumps(bundle_for_hash, sort_keys=True, indent=2, default=str)
    bundle["bundle_sha256_self"] = hashlib.sha256(raw.encode()).hexdigest()
    path = Path(tmpdir) / "model_bundle.json"
    path.write_text(json.dumps(bundle, sort_keys=True, indent=2, default=str) + "\n")
    return path


# ---------------------------------------------------------------------------
# CLI --help
# ---------------------------------------------------------------------------
class TestCLIHelp:
    def test_help_works(self):
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--help"])
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# CLI --once required
# ---------------------------------------------------------------------------
class TestCLIOnceRequired:
    def test_once_required(self):
        tmpdir = tempfile.mkdtemp()
        bundle = _write_bundle(tmpdir)
        bars = _make_bars("BTC", n=50)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        with pytest.raises(SystemExit) as exc:
            _parse_args([
                "--model-bundle", str(bundle),
                "--bars-path", str(bars_path),
                "--no-funding",
            ])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# CLI --funding-path validation
# ---------------------------------------------------------------------------
class TestCLIFundingPath:
    def test_funding_path_required_without_no_funding(self):
        tmpdir = tempfile.mkdtemp()
        bundle = _write_bundle(tmpdir)
        bars = _make_bars("BTC", n=50)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        with pytest.raises(SystemExit) as exc:
            _parse_args([
                "--model-bundle", str(bundle),
                "--bars-path", str(bars_path),
                "--once",
            ])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# CLI --symbol validation
# ---------------------------------------------------------------------------
class TestCLISymbol:
    def test_unknown_symbol_fails(self):
        tmpdir = tempfile.mkdtemp()
        bundle = _write_bundle(tmpdir)
        bars = _make_bars("BTC", n=50)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        with pytest.raises(SystemExit) as exc:
            _parse_args([
                "--model-bundle", str(bundle),
                "--bars-path", str(bars_path),
                "--no-funding", "--once",
                "--symbol", "LINK",
            ])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# CLI --dry-run
# ---------------------------------------------------------------------------
class TestCLIDryRun:
    def test_dry_run_accepted(self):
        tmpdir = tempfile.mkdtemp()
        bundle = _write_bundle(tmpdir)
        bars = _make_bars("BTC", n=50)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        args = _parse_args([
            "--model-bundle", str(bundle),
            "--bars-path", str(bars_path),
            "--no-funding", "--once", "--dry-run",
        ])
        assert args.dry_run is True


# ---------------------------------------------------------------------------
# CLI --state-recovery permissive
# ---------------------------------------------------------------------------
class TestCLIStateRecovery:
    def test_permissive_requires_ikwad(self):
        tmpdir = tempfile.mkdtemp()
        bundle = _write_bundle(tmpdir)
        bars = _make_bars("BTC", n=50)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        with pytest.raises(SystemExit) as exc:
            _parse_args([
                "--model-bundle", str(bundle),
                "--bars-path", str(bars_path),
                "--no-funding", "--once",
                "--state-recovery", "permissive",
            ])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# Forbidden strings
# ---------------------------------------------------------------------------
class TestForbiddenStrings:
    def test_no_order_private_key_in_runner(self):
        import run_hyperliquid_btc_eth_ml_atr_paper_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in ["submit_order", "place_order", "cancel_order", "private_key", "broker_connect"]:
                assert forbidden not in stripped
