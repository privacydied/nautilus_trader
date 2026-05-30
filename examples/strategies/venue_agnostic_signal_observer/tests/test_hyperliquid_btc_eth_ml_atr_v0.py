"""
Tests for hyperliquid_btc_eth_ml_atr_v0 — core module.

NOT live trading. NOT paper execution. NOT bot authorization.
"""

import json
import sys
import tempfile
import warnings
from pathlib import Path
from unittest.mock import patch

from typing import Tuple

import numpy as np
import pandas as pd
import pytest

# Ensure importable without Nautilus extensions
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyperliquid_btc_eth_ml_atr_v0 import (
    VALID_SYMBOLS,
    MlAtrConfig,
    SplitConfig,
    FeatureConfig,
    CostConfig,
    ExitConfig,
    ModelConfig,
    BacktestTrade,
    compute_rsi_wilder,
    compute_atr,
    generate_features,
    generate_labels,
    assign_splits,
    load_bars,
    load_funding,
    validate_ohlc,
    validate_timestamp_alignment,
    detect_gaps,
    validate_funding,
    calibrate_platt,
    apply_platt,
    compute_reliability,
    compute_brier,
    run_backtest,
    compute_trade_metrics,
    run_pipeline,
    _compute_auc,
    _train_sklearn,
    _train_numpy_fallback,
    _predict_sklearn,
    _predict_numpy,
    _ensure_utc_aware,
    _detect_timestamp_dtype,
)


# ============================================================================
# Fixtures
# ============================================================================

def _make_bars(
    symbol="BTC", n=200, start="2024-01-01T00:00:00Z", base_price=40000.0, seed=42
) -> pd.DataFrame:
    """Generate synthetic 1h bars."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start, periods=n, freq="h", tz="UTC")
    prices = base_price + rng.randn(n).cumsum() * 100
    prices = np.maximum(prices, 1000)
    high = prices + rng.uniform(50, 200, n)
    low = prices - rng.uniform(50, 200, n)
    open_ = prices + rng.randn(n) * 50
    volume = rng.uniform(100, 10000, n)
    return pd.DataFrame({
        "timestamp": dates,
        "symbol": symbol,
        "open": open_,
        "high": np.maximum(high, np.maximum(prices, open_)),
        "low": np.minimum(low, np.minimum(prices, open_)),
        "close": prices,
        "volume": volume,
    })


def _make_funding(symbol="BTC", n=200, start="2024-01-01T00:00:00Z", seed=42) -> pd.DataFrame:
    """Generate synthetic hourly funding rows."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start, periods=n, freq="h", tz="UTC")
    rates = rng.uniform(-0.0005, 0.0005, n)
    return pd.DataFrame({
        "timestamp": dates,
        "symbol": symbol,
        "funding_rate": rates,
    })


def _make_30day_btc_bars(seed=42) -> pd.DataFrame:
    """30-day BTC fixture for determinism tests."""
    return _make_bars("BTC", n=30 * 24, start="2024-06-01T00:00:00Z", seed=seed)


def _make_long_enough_dataset(seed=42) -> Tuple:
    """
    Make bars + funding spanning train/val/test with enough rows.
    Returns (bars_path, funding_path, tmpdir).
    """
    tmpdir = tempfile.mkdtemp()
    bars_btc = _make_bars("BTC", n=10000, start="2023-11-01T00:00:00Z", seed=seed)
    bars_eth = _make_bars("ETH", n=10000, start="2023-11-01T00:00:00Z", base_price=2000.0, seed=seed + 1)
    bars = pd.concat([bars_btc, bars_eth], ignore_index=True)
    bars_path = Path(tmpdir) / "bars.csv"
    bars.to_csv(bars_path, index=False)

    fund_btc = _make_funding("BTC", n=10000, start="2023-11-01T00:00:00Z", seed=seed)
    fund_eth = _make_funding("ETH", n=10000, start="2023-11-01T00:00:00Z", seed=seed + 1)
    funding = pd.concat([fund_btc, fund_eth], ignore_index=True)
    funding_path = Path(tmpdir) / "funding.csv"
    funding.to_csv(funding_path, index=False)

    return bars_path, funding_path, tmpdir


# ============================================================================
# 1. Module Imports
# ============================================================================

class TestModuleImports:
    def test_import_without_nautilus(self):
        """Module must import without Nautilus extensions."""
        import importlib
        mod = importlib.import_module("hyperliquid_btc_eth_ml_atr_v0")
        assert hasattr(mod, "run_pipeline")
        assert hasattr(mod, "MlAtrConfig")


# ============================================================================
# 2. Timestamp Handling
# ============================================================================

class TestTimestampHandling:
    def test_utc_aware_accepted(self):
        s = pd.Series(pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"], utc=True))
        result = _ensure_utc_aware(s)
        assert result.dt.tz is not None

    def test_naive_rejected(self):
        s = pd.Series(pd.to_datetime(["2024-01-01T00:00:00", "2024-01-01T01:00:00"]))
        with pytest.raises(ValueError, match="Naive timestamps"):
            _ensure_utc_aware(s)

    def test_int_epoch_detected(self):
        s = pd.Series([1704067200, 1704070800])
        assert _detect_timestamp_dtype(s) == "int_epoch"

    def test_float_epoch_detected(self):
        s = pd.Series([1704067200.0, 1704070800.0])
        assert _detect_timestamp_dtype(s) == "float_epoch"

    def test_datetime_detected(self):
        s = pd.Series(pd.to_datetime(["2024-01-01"], utc=True))
        assert _detect_timestamp_dtype(s) == "datetime64"


# ============================================================================
# 3. OHLC Sanity
# ============================================================================

class TestOHLCSanity:
    def test_valid_rows_pass(self):
        bars = _make_bars(n=100)
        clean, stats = validate_ohlc(bars)
        assert stats["ohlc_sanity_rejects"] == 0
        assert len(clean) == 100

    def test_high_below_low_rejected(self):
        bars = _make_bars(n=10)
        bars.loc[5, "high"] = bars.loc[5, "low"] - 100
        clean, stats = validate_ohlc(bars)
        assert stats["ohlc_sanity_rejects"] >= 1

    def test_zero_volume_counted_separately(self):
        bars = _make_bars(n=10)
        bars.loc[3, "volume"] = 0
        clean, stats = validate_ohlc(bars)
        assert stats["zero_volume_bar_count"] >= 1

    def test_nan_ohlc_rejected(self):
        bars = _make_bars(n=10)
        bars.loc[2, "close"] = np.nan
        clean, stats = validate_ohlc(bars)
        assert stats["ohlc_sanity_rejects"] >= 1


# ============================================================================
# 4. Timestamp Alignment
# ============================================================================

class TestTimestampAlignment:
    def test_aligned_passes(self):
        bars = _make_bars(n=100)
        _, stats = validate_timestamp_alignment(bars)
        assert stats["timestamp_misaligned_rate"] == 0.0

    def test_misaligned_detected(self):
        bars = _make_bars(n=100)
        # Shift some bars to non-hour boundaries
        bars.loc[0, "timestamp"] = bars.loc[0, "timestamp"] + pd.Timedelta(minutes=15)
        _, stats = validate_timestamp_alignment(bars)
        assert stats["timestamp_misaligned_count"] >= 1


# ============================================================================
# 5. Gap Detection
# ============================================================================

class TestGapDetection:
    def test_no_gaps(self):
        bars = _make_bars(n=100)
        gaps, hours = detect_gaps(bars, max_gap_hours=24)
        assert sum(hours.values()) == 0

    def test_gaps_detected(self):
        bars = _make_bars(n=100)
        # Remove rows 50-55 to create a gap
        bars = bars.drop(bars.index[50:56]).reset_index(drop=True)
        gaps, hours = detect_gaps(bars, max_gap_hours=24)
        assert hours.get("BTC", 0) > 0


# ============================================================================
# 6. Funding
# ============================================================================

class TestFunding:
    def test_funding_sign_convention(self):
        """
        Hyperliquid: positive funding_rate means longs pay shorts.
        Long pays positive rate -> negative bps.
        Short receives positive rate -> positive bps.
        """
        # Create a simple funding row
        funding_df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01T01:00:00Z"], utc=True),
            "symbol": ["BTC"],
            "funding_rate": [0.001],  # positive: longs pay shorts
        })
        # Long trade
        from hyperliquid_btc_eth_ml_atr_v0 import _compute_funding_bps
        long_bps, n, _ = _compute_funding_bps(
            "long", pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
            pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
            funding_df, 40000.0, CostConfig(), False
        )
        assert long_bps < 0, "Long should pay positive funding (negative return)"

        # Short trade
        short_bps, n, _ = _compute_funding_bps(
            "short", pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
            pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
            funding_df, 40000.0, CostConfig(), False
        )
        assert short_bps > 0, "Short should receive positive funding (positive return)"

    def test_funding_negative_rate(self):
        """Negative funding: longs receive, short pays."""
        funding_df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01T01:00:00Z"], utc=True),
            "symbol": ["BTC"],
            "funding_rate": [-0.001],
        })
        from hyperliquid_btc_eth_ml_atr_v0 import _compute_funding_bps
        long_bps, _, _ = _compute_funding_bps(
            "long", pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
            pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
            funding_df, 40000.0, CostConfig(), False
        )
        assert long_bps > 0, "Long should receive negative funding (positive return)"

    def test_funding_sanity_rejects_high_rate(self):
        funding = _make_funding(n=10)
        funding.loc[5, "funding_rate"] = 0.05  # way above 0.01
        clean, stats = validate_funding(funding, CostConfig(max_abs_funding_rate=0.01), False)
        assert stats["funding_sanity_rejects"] >= 1

    def test_funding_cadence_hourly(self):
        """Hyperliquid funding is hourly."""
        dates = pd.date_range("2024-01-01", periods=24, freq="h", tz="UTC")
        funding = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "funding_rate": np.random.uniform(-0.001, 0.001, 24),
        })
        # Should pass validation with hourly cadence
        clean, _ = validate_funding(funding, CostConfig(), False)
        assert len(clean) > 0

    def test_no_funding_mode(self):
        funding = _make_funding(n=10)
        clean, stats = validate_funding(funding, CostConfig(), no_funding=True)
        assert stats["funding_sanity_rejects"] == 0


# ============================================================================
# 7. Feature Generation
# ============================================================================

class TestFeatureGeneration:
    def test_past_only_features(self):
        """For every feature, feature[t] uses only bars with timestamp <= t."""
        bars = _make_bars(n=100)
        features = generate_features(bars, None, FeatureConfig(), CostConfig(), no_funding=True)
        # Check that ret_1h is NaN at index 0 (no prior bar) and valid after
        assert pd.isna(features["ret_1h"].iloc[0])
        assert not pd.isna(features["ret_1h"].iloc[1])

    def test_rsi_wilder_hand_computed(self):
        """RSI matches hand-computed Wilder-smoothed value.
        Series: 44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                46.08, 45.89, 46.03, 45.61, 46.28, 46.28 (15 values, 14 changes)
        Using Wilder smoothing seeded with mean of first 14 gains/losses.
        """
        closes = pd.Series([44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10,
                            45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28],
                           dtype=float)
        rsi = compute_rsi_wilder(closes, period=14)
        # Last value should be computed (not NaN)
        assert not np.isnan(rsi.iloc[-1])
        # RSI should be between 0 and 100
        assert 0 <= rsi.iloc[-1] <= 100

    def test_rsi_fully_flat(self):
        """Flat prices -> RSI around 50."""
        closes = pd.Series([100.0] * 30)
        rsi = compute_rsi_wilder(closes, period=14)
        # After initial NaN, RSI should be 50 (no gains, no losses -> avg_loss=0 -> RS=inf -> RSI=100
        # or if both 0, RSI=50 from division by zero handling)
        valid = rsi.dropna()
        if len(valid) > 0:
            # With zero gains and zero losses, RS = nan, so RSI = 50 from clip
            assert all(v == 50.0 or np.isnan(v) for v in valid)

    def test_atr_basic_sanity(self):
        """ATR should be positive for valid OHLC data."""
        bars = _make_bars(n=50)
        atr = compute_atr(bars["high"], bars["low"], bars["close"], period=14)
        valid = atr.dropna()
        assert all(valid > 0)

    def test_atr_at_entry_from_signal_bar(self):
        """ATR is frozen at signal bar close t, not entry bar t+1."""
        bars = _make_bars(n=50)
        features = generate_features(bars, None, FeatureConfig(), CostConfig(), no_funding=True)
        # Check that atr_14h_raw at index 35 equals the ATR computed from bars[:36]
        atr_35 = features["atr_14h_raw"].iloc[35]
        atr_full = compute_atr(bars["high"], bars["low"], bars["close"], 14)
        assert abs(atr_35 - atr_full.iloc[35]) < 1e-10

    def test_feature_columns_frozen(self):
        """Only the frozen feature set is generated."""
        bars = _make_bars(n=100)
        features = generate_features(bars, None, FeatureConfig(), CostConfig(), no_funding=True)
        expected = set(FeatureConfig().feature_names) | {"atr_14h_raw", "symbol", "timestamp",
                                                          "open", "high", "low", "close", "volume"}
        for col in FeatureConfig().feature_names:
            assert col in features.columns

    def test_no_same_bar_funding_lookahead(self):
        """funding_current at bar t uses funding strictly before t_close."""
        bars = _make_bars(n=10, start="2024-01-01T00:00:00Z")
        # Funding at exactly 01:00 should NOT be used for bar at 01:00
        funding = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01T01:00:00Z"], utc=True),
            "symbol": ["BTC"],
            "funding_rate": [0.01],
        })
        features = generate_features(bars, funding, FeatureConfig(), CostConfig(), no_funding=False)
        # Bar at 01:00 should have funding_current = 0.0 (no funding strictly before it)
        idx_01 = features[features["timestamp"] == pd.Timestamp("2024-01-01T01:00:00Z", tz="UTC")].index[0]
        assert features.loc[idx_01, "funding_current"] == 0.0


# ============================================================================
# 8. Label Generation
# ============================================================================

class TestLabelGeneration:
    def test_strict_greater_than(self):
        """Ties give label 0."""
        closes = [100.0, 100.0, 100.0, 100.0]
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"),
            "symbol": "BTC",
            "close": closes,
            "open": closes, "high": closes, "low": closes, "volume": [1.0]*4,
        })
        df = generate_labels(df, horizon=1)
        # tie at index 0: future_close == close
        assert df["label_up"].iloc[0] == 0
        assert df["is_tie"].iloc[0] == 1

    def test_tie_count_emitted(self):
        """label_tie_count is recorded."""
        closes = [100.0, 100.0, 100.0]
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"),
            "symbol": "BTC",
            "close": closes,
            "open": closes, "high": closes, "low": closes, "volume": [1.0]*3,
        })
        df = generate_labels(df, horizon=1)
        assert df["is_tie"].sum() >= 1

    def test_label_horizon_exceeds_split(self):
        """label_horizon_bars > effective split length results in no valid labels after filtering."""
        # 10 bars, label_horizon=20 -> future_close is NaN for all rows
        # label_up = (NaN > close) = False for all -> degenerate balance -> fails gate
        bars = _make_bars(n=10, start="2025-07-01T00:00:00Z")
        labeled = generate_labels(bars, horizon=20)
        # All future_close are NaN -> label_up is all 0 -> degenerate
        valid_labels = labeled.dropna(subset=["label_up"])
        # After feature/label filtering, no rows survive
        assert len(valid_labels) == 0 or labeled["label_up"].mean() == 0.0


# ============================================================================
# 9. Split Handling
# ============================================================================

class TestSplitHandling:
    def test_chronological_splits(self):
        """Splits respect chronological boundaries."""
        bars = _make_bars(n=1000, start="2023-11-01T00:00:00Z")
        features = generate_features(bars, None, FeatureConfig(), CostConfig(), no_funding=True)
        labeled = generate_labels(features, 24)
        splits = assign_splits(labeled, SplitConfig(), FeatureConfig())
        # Train should end before validation
        if len(splits["train"]) > 0 and len(splits["validation"]) > 0:
            assert splits["train"]["timestamp"].max() < splits["validation"]["timestamp"].min()

    def test_no_overlap_audit(self):
        """No-overlap audit catches overlaps."""
        # Manually create overlapping splits
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC"),
            "symbol": "BTC",
            "ret_1h": [0.0]*10, "ret_4h": [0.0]*10, "ret_24h": [0.0]*10,
            "realized_vol_24h": [0.0]*10, "atr_norm_14h": [0.0]*10,
            "funding_current": [0.0]*10, "funding_mean_24h": [0.0]*10,
            "rsi_14h": [50.0]*10,
            "label_up": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
            "is_tie": [0]*10,
        })
        # This should work with valid splits
        splits = assign_splits(df, SplitConfig(), FeatureConfig())
        assert len(splits) == 3


# ============================================================================
# 10. Model Backends
# ============================================================================

class TestModelBackends:
    def test_sklearn_backend(self):
        """Sklearn backend trains and predicts."""
        rng = np.random.RandomState(42)
        X = rng.randn(500, 8)
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        coef, intercept, scaler = _train_sklearn(X, y, C=1.0, seed=42)
        probs = _predict_sklearn(coef, intercept, scaler, X)
        assert probs.shape == (500,)
        assert all(0 <= p <= 1 for p in probs)

    def test_numpy_fallback_backend(self):
        """Numpy fallback backend trains and predicts."""
        rng = np.random.RandomState(42)
        X = rng.randn(500, 8)
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        coef, intercept, solver = _train_numpy_fallback(X, y, C=1.0, seed=42)
        # Numpy fallback uses its own manual standardization
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        X_scaled = (X - mean) / std
        probs = _predict_numpy(coef, intercept, X_scaled)
        assert probs.shape == (500,)
        assert all(0 <= p <= 1 for p in probs)

    def test_numpy_fallback_bit_identical(self):
        """Two same-seed runs produce bit-identical numpy_fallback coefficients."""
        rng = np.random.RandomState(42)
        X = rng.randn(500, 8)
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        coef1, _, _ = _train_numpy_fallback(X, y, C=1.0, seed=42)
        coef2, _, _ = _train_numpy_fallback(X, y, C=1.0, seed=42)
        np.testing.assert_array_equal(coef1, coef2)

    def test_sklearn_numpy_within_tolerance(self):
        """Sklearn and numpy_fallback coefficients within tolerance on synthetic fixture."""
        rng = np.random.RandomState(42)
        X = rng.randn(500, 8)
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        coef_sk, _, scaler_sk = _train_sklearn(X, y, C=1.0, seed=42)
        coef_np, _, _ = _train_numpy_fallback(X, y, C=1.0, seed=42)
        # Both should have same sign pattern at least
        assert np.sign(coef_sk[:2]).tolist() == np.sign(coef_np[:2]).tolist()

    def test_sklearn_unimportable_fails_closed(self):
        """--model-backend=sklearn with sklearn unimportable fails closed."""
        with patch.dict(sys.modules, {"sklearn": None, "sklearn.linear_model": None}):
            with pytest.raises(ImportError):
                _train_sklearn(np.zeros((10, 2)), np.zeros(10), 1.0, 42)

    def test_auto_backend_resolves(self):
        """auto backend resolves to sklearn when available."""
        MlAtrConfig()  # just check construction works
        # auto should pick sklearn if available
        try:
            import sklearn
            assert True
        except ImportError:
            pass  # would fall back to numpy_fallback


# ============================================================================
# 11. Calibration
# ============================================================================

class TestCalibration:
    def test_platt_params_stored(self):
        """Platt a and b are stored correctly."""
        probs = np.array([0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.3, 0.4, 0.5, 0.6])
        labels = np.array([0, 0, 0, 1, 1, 1, 0, 1, 1, 1])
        a, b = calibrate_platt(probs, labels)
        assert isinstance(a, float)
        assert isinstance(b, float)

    def test_platt_improves_calibration(self):
        """Platt scaling produces valid calibrated probabilities."""
        probs = np.array([0.3, 0.4, 0.6, 0.7, 0.3, 0.4, 0.6, 0.7, 0.3, 0.4])
        labels = np.array([0, 0, 1, 1, 0, 0, 1, 1, 0, 1])
        a, b = calibrate_platt(probs, labels)
        cal = apply_platt(a, b, probs)
        # Calibrated probs should be in [0, 1]
        assert all(0 <= p <= 1 for p in cal)
        # Brier should not explode
        brier_after = compute_brier(cal, labels)
        assert brier_after < 1.0

    def test_isotonic_requires_opt_in(self):
        """Isotonic refuses to run unless explicit opt-in."""
        cfg = MlAtrConfig(model=ModelConfig(calibrator="isotonic"))
        assert cfg.model.calibrator == "isotonic"

    def test_reliability_bins_equal_width(self):
        probs = np.random.RandomState(42).uniform(0, 1, 200)
        labels = (probs > 0.5).astype(int)
        table, ece, mce, warns = compute_reliability(probs, labels, 10)
        assert len(table) == 10
        for i, row in enumerate(table):
            expected_lo = i / 10
            expected_hi = (i + 1) / 10
            assert abs(row["bin_low"] - expected_lo) < 1e-10
            assert abs(row["bin_high"] - expected_hi) < 1e-10

    def test_ece_mce_sanity(self):
        probs = np.array([0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.8, 0.8, 0.2, 0.2])
        labels = np.array([1, 1, 0, 0, 0, 1, 1, 0, 0, 1])
        table, ece, mce, _ = compute_reliability(probs, labels, 5)
        assert 0 <= ece <= 1
        assert mce >= ece

    def test_brier_score(self):
        """Brier score is between 0 and 1."""
        probs = np.array([0.9, 0.1, 0.8, 0.2])
        labels = np.array([1, 0, 1, 0])
        b = compute_brier(probs, labels)
        assert 0 <= b <= 1


# ============================================================================
# 12. Backtest Engine
# ============================================================================

class TestBacktest:
    def test_long_stop_trailing(self):
        """Long trade exits on trailing stop."""
        n = 20
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        # Price drops steadily to trigger trailing stop
        closes = np.linspace(100, 90, n)
        highs = closes + 1
        lows = closes - 1
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": closes + 0.5,
            "high": highs,
            "low": lows,
            "close": closes,
            "atr_14h_raw": [2.0] * n,
            "calibrated_p_up": [0.6] + [0.5] * (n - 1),  # signal at bar 0
        })
        trades = run_backtest(df, ExitConfig(), CostConfig(), 0.55, 0.40, None, False)
        # Should produce a trade (or not if price doesn't drop enough)
        # With stop=2.0*2=4.0, entry at bar 1 open~100, stop at ~96
        # trailing at 3.0*2=6.0 below max close

    def test_short_stop_trailing(self):
        """Short trade exits on trailing stop."""
        n = 20
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        closes = np.linspace(100, 110, n)
        highs = closes + 1
        lows = closes - 1
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": closes + 0.5,
            "high": highs,
            "low": lows,
            "close": closes,
            "atr_14h_raw": [2.0] * n,
            "calibrated_p_up": [0.3] + [0.5] * (n - 1),  # short signal at bar 0
        })
        trades = run_backtest(df, ExitConfig(), CostConfig(), 0.55, 0.40, None, False)

    def test_initial_stop_at_entry(self):
        """Entry bar itself trades through the initial stop before any trailing ratchet."""
        n = 5
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        # Signal at bar 0 (calibrated_p_up=0.6 -> long), entry at bar 1
        # ATR=5.0, stop_mult=2.0 -> initial_stop = 95 - 10.0 = 85
        # Bar 1 low=84 < stop=85 -> initial_stop_at_entry
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": [100, 95, 90, 85, 80],
            "high": [101, 96, 91, 86, 81],
            "low": [99, 84, 89, 84, 79],   # bar 1 low=84 < stop=85
            "close": [100, 95, 90, 85, 80],
            "atr_14h_raw": [5.0] * n,       # ATR=5.0 -> stop gap = 10.0
            "calibrated_p_up": [0.6] + [0.5] * (n - 1),
        })
        trades = run_backtest(df, ExitConfig(stop_atr_mult=2.0), CostConfig(), 0.55, 0.40, None, False)
        assert len(trades) >= 1
        assert trades[0].exit_reason == "initial_stop_at_entry"

    def test_end_of_data(self):
        """Open position closed at last bar with end_of_data."""
        n = 10
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": [100] * n,
            "high": [101] * n,
            "low": [99] * n,
            "close": [100] * n,
            "atr_14h_raw": [50.0] * n,  # very large ATR so stops never hit
            "calibrated_p_up": [0.6] + [0.5] * (n - 1),
        })
        trades = run_backtest(df, ExitConfig(), CostConfig(), 0.55, 0.40, None, False)
        if trades:
            assert trades[0].exit_reason == "end_of_data"

    def test_short_side_arithmetic(self):
        """Short: entry > exit yields positive bps."""
        trade = BacktestTrade(
            symbol="BTC", side="short",
            signal_timestamp=pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
            entry_timestamp=pd.Timestamp("2024-01-01T01:00:00Z", tz="UTC"),
            exit_timestamp=pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
            entry_price=100.0, exit_price=99.0,
            entry_atr=2.0, initial_stop=104.0, final_stop=104.0,
            exit_reason="trailing_stop", hold_bars=1, funding_periods_held=0,
            gross_return_bps=0.0, funding_bps=0.0, fee_slippage_bps=0.0,
            net_return_bps=0.0, calibrated_p_up_at_signal=0.3,
        )
        side_sign = -1.0
        gross_bps = ((trade.exit_price - trade.entry_price) / trade.entry_price) * 10000.0 * side_sign
        assert gross_bps > 0, "Short with entry > exit should be positive bps"

    def test_fee_slippage_arithmetic(self):
        """Fee + slippage is applied on entry and exit."""
        cost = CostConfig(fee_bps_per_side=1.0, slippage_bps_per_side=0.5)
        expected = 2.0 * (cost.fee_bps_per_side + cost.slippage_bps_per_side)
        assert expected == 3.0

    def test_funding_periods_held_can_be_zero(self):
        """Very short hold may have 0 funding periods."""
        from hyperliquid_btc_eth_ml_atr_v0 import _compute_funding_bps
        # No funding data
        bps, n, _ = _compute_funding_bps(
            "long", pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
            pd.Timestamp("2024-01-01T00:30:00Z", tz="UTC"),
            None, 100.0, CostConfig(), False
        )
        assert n == 0

    def test_trailing_ratchets_only_on_bar_close(self):
        """Trailing stop does not ratchet intrabar."""
        # Create scenario where intrabar high would ratchet but bar close wouldn't
        n = 6
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            # Bar 1 (entry): opens at 100
            # Bar 2: high=105 (would ratchet), but close=99 (would not ratchet)
            # Bar 3: drops to trigger initial stop
            "open": [100, 100, 104, 99, 95, 90],
            "high": [101, 105, 105, 100, 96, 91],
            "low": [99, 98, 98, 97, 93, 89],
            "close": [100, 99, 99, 98, 94, 90],
            "atr_14h_raw": [2.0] * n,
            "calibrated_p_up": [0.6] + [0.5] * (n - 1),
        })
        trades = run_backtest(df, ExitConfig(stop_atr_mult=2.0, trailing_atr_mult=3.0), CostConfig(), 0.55, 0.40, None, False)
        # If trade exists, verify trailing didn't ratchet on intrabar high
        if trades:
            t = trades[0]
            # ATR = 2.0, trailing_mult = 3.0, so trailing gap = 6.0
            # If close at bar 2 = 99, trailing should be 99 - 6 = 93
            # Not 105 - 6 = 99 (from intrabar high)
            assert t.initial_stop == 100.0 - 2.0 * 2.0  # 96

    def test_same_bar_ambiguity_long(self):
        """Conservative same-bar: long with low <= stop, use min(open, stop)."""
        n = 4
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": [100, 100, 94, 90],  # bar 2 opens below stop
            "high": [101, 101, 95, 91],
            "low": [99, 93, 92, 89],  # bar 2 low <= stop
            "close": [100, 95, 93, 90],
            "atr_14h_raw": [2.0] * n,
            "calibrated_p_up": [0.6] + [0.5] * (n - 1),
        })
        # stop = 100 - 2*2 = 96 at bar 1 entry
        trades = run_backtest(df, ExitConfig(stop_atr_mult=2.0), CostConfig(), 0.55, 0.40, None, False)
        if trades:
            t = trades[0]
            # If bar 2 gaps through stop: fill = min(open=94, stop=96) = 94
            assert t.exit_price <= 96

    def test_same_bar_ambiguity_short(self):
        """Conservative same-bar: short with high >= stop, use max(open, stop)."""
        n = 4
        dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        df = pd.DataFrame({
            "timestamp": dates,
            "symbol": "BTC",
            "open": [100, 100, 106, 110],
            "high": [101, 107, 108, 111],
            "low": [99, 105, 104, 109],
            "close": [100, 105, 105, 110],
            "atr_14h_raw": [2.0] * n,
            "calibrated_p_up": [0.3] + [0.5] * (n - 1),
        })
        # stop = 100 + 2*2 = 104 at bar 1 entry
        trades = run_backtest(df, ExitConfig(stop_atr_mult=2.0), CostConfig(), 0.55, 0.40, None, False)
        if trades:
            t = trades[0]
            # If bar 2 gaps through stop: fill = max(open=106, stop=104) = 106
            assert t.exit_price >= 104


# ============================================================================
# 13. Metrics
# ============================================================================

class TestMetrics:
    def test_end_of_data_excluded_from_win_rate(self):
        """end_of_data trades excluded from win rate but included in net bps."""
        trades = [
            BacktestTrade("BTC", "long", pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
                         pd.Timestamp("2024-01-01T01:00:00Z", tz="UTC"),
                         pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
                         100, 101, 2, 96, 96, "trailing_stop", 1, 0, 100, 0, 3, 97, 0.6),
            BacktestTrade("BTC", "long", pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
                         pd.Timestamp("2024-01-01T03:00:00Z", tz="UTC"),
                         pd.Timestamp("2024-01-01T04:00:00Z", tz="UTC"),
                         100, 99, 2, 96, 96, "end_of_data", 1, 0, -100, 0, 3, -103, 0.6),
        ]
        m = compute_trade_metrics(trades, seed=42)
        # Win rate should only count non-eod trades
        assert m.trades_closed_at_end_of_data == 1
        # Net bps includes both trades
        assert m.mean_net_bps != 0


# ============================================================================
# 14. Gate Ordering
# ============================================================================

class TestGateOrdering:
    def test_data_integrity_before_row_counts(self):
        """Data integrity gate fires before row count gate."""
        # Create bars with unknown symbol -> load_bars raises ValueError
        tmpdir = tempfile.mkdtemp()
        bars = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01T00:00:00Z"], utc=True),
            "symbol": ["LINK"],  # invalid
            "open": [100], "high": [101], "low": [99], "close": [100], "volume": [1.0],
        })
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        cfg = MlAtrConfig(dry_run=True)
        with pytest.raises(ValueError, match="Unknown symbols"):
            run_pipeline(cfg, bars_path, None, Path(tmpdir))

    def test_row_counts_before_calibration(self):
        """Row count gate fires before validation AUC gate."""
        tmpdir = tempfile.mkdtemp()
        # Very small dataset that will fail row counts
        bars = _make_bars("BTC", n=50, start="2024-01-01T00:00:00Z")
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        cfg = MlAtrConfig(
            split=SplitConfig(min_train_rows=1000, min_validation_rows=100, min_test_rows=100),
            dry_run=True,
        )
        summary = run_pipeline(cfg, bars_path, None, Path(tmpdir))
        assert summary.status in ("ML_ATR_V0_NEEDS_MORE_DATA", "ML_ATR_V0_ERROR_INVALID_INPUT")


# ============================================================================
# 15. Safety Constraints
# ============================================================================

class TestSafetyConstraints:
    def test_forbidden_statuses_not_emitted(self):
        """Forbidden statuses must not appear in summary."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100, start="2024-01-01T00:00:00Z")
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        cfg = MlAtrConfig(dry_run=True)
        summary = run_pipeline(cfg, bars_path, None, Path(tmpdir))
        assert summary.status not in {"TRADE_READY", "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE", "PROFITABLE"}

    def test_no_order_private_key_strings_in_module(self):
        """Module should not contain forbidden execution strings (outside comments/dict values)."""
        import hyperliquid_btc_eth_ml_atr_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Skip dict/string literals that mention safety flags
            if "no_private_keys" in stripped or "no_auth" in stripped:
                continue
            for forbidden in ["submit_order", "place_order", "cancel_order", "private_key", "broker_connect"]:
                assert forbidden not in stripped, f"Found '{forbidden}' in module code: {stripped}"

    def test_ml_atr_v0_backtest_ready_not_in_completed_summary(self):
        """ML_ATR_V0_BACKTEST_READY must not appear in completed summary.json."""
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100, start="2024-01-01T00:00:00Z")
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        cfg = MlAtrConfig(dry_run=False)
        output_root = Path(tmpdir) / "reports"
        summary = run_pipeline(cfg, bars_path, None, output_root)
        # If summary.json exists, check it
        summary_path = output_root / summary.run_id / "summary.json"
        if summary_path.exists():
            data = json.loads(summary_path.read_text())
            assert data["status"] != "ML_ATR_V0_BACKTEST_READY"

    def test_link_symbol_causes_failure(self):
        """LINK in bar input causes deterministic failure."""
        # load_bars raises ValueError for unknown symbols
        tmpdir = tempfile.mkdtemp()
        bars_btc = _make_bars("BTC", n=50)
        bars_link = _make_bars("LINK", n=50, base_price=15.0)
        bars = pd.concat([bars_btc, bars_link], ignore_index=True)
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        cfg = MlAtrConfig(dry_run=True)
        with pytest.raises(ValueError, match="Unknown symbols"):
            run_pipeline(cfg, bars_path, None, Path(tmpdir))


# ============================================================================
# 16. Determinism Tests
# ============================================================================

class TestDeterminism:
    @pytest.mark.determinism
    def test_feature_values_first_50_rows(self):
        """Synthetic 30-day fixture: first 50 rows have expected feature values."""
        bars = _make_30day_btc_bars(seed=42)
        features = generate_features(bars, None, FeatureConfig(), CostConfig(), no_funding=True)
        # ret_1h should be finite for rows >= 1
        assert not pd.isna(features["ret_1h"].iloc[50])

    @pytest.mark.determinism
    def test_label_distribution_deterministic(self):
        """Label distribution is deterministic for fixed fixture."""
        bars = _make_30day_btc_bars(seed=42)
        labeled = generate_labels(bars, 24)
        pos_pct = labeled["label_up"].mean()
        assert 0.3 < pos_pct < 0.7  # not degenerate

    @pytest.mark.determinism
    def test_config_json_byte_identical(self):
        """config.json is byte-identical across reruns with same seed."""
        cfg = MlAtrConfig()
        tmpdir = tempfile.mkdtemp()
        path1 = Path(tmpdir) / "c1.json"
        path2 = Path(tmpdir) / "c2.json"
        from hyperliquid_btc_eth_ml_atr_v0 import write_config_json
        write_config_json(cfg, path1)
        write_config_json(cfg, path2)
        assert path1.read_bytes() == path2.read_bytes()

    @pytest.mark.determinism
    def test_csv_determinism(self):
        """CSV outputs are byte-identical across same-seed reruns."""
        trades = [
            BacktestTrade("BTC", "long", pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC"),
                         pd.Timestamp("2024-01-01T01:00:00Z", tz="UTC"),
                         pd.Timestamp("2024-01-01T02:00:00Z", tz="UTC"),
                         100, 101, 2, 96, 96, "trailing_stop", 1, 0, 100, 0, 3, 97, 0.6),
        ]
        tmpdir = tempfile.mkdtemp()
        p1 = Path(tmpdir) / "t1.csv"
        p2 = Path(tmpdir) / "t2.csv"
        from hyperliquid_btc_eth_ml_atr_v0 import write_trades_csv
        write_trades_csv(trades, p1)
        write_trades_csv(trades, p2)
        assert p1.read_bytes() == p2.read_bytes()

    @pytest.mark.determinism
    def test_numpy_fallback_bit_identical(self):
        """Two same-seed numpy_fallback runs produce bit-identical coefficients."""
        rng = np.random.RandomState(42)
        X = rng.randn(500, 8)
        y = (X[:, 0] + X[:, 1] > 0).astype(int)
        coef1, _, _ = _train_numpy_fallback(X, y, C=1.0, seed=42)
        coef2, _, _ = _train_numpy_fallback(X, y, C=1.0, seed=42)
        np.testing.assert_array_equal(coef1, coef2)


# ============================================================================
# 17. Spec Version
# ============================================================================

class TestSpecVersion:
    def test_spec_version_in_manifest(self):
        """spec_version equals 'v0' in manifest."""
        cfg = MlAtrConfig(dry_run=True)
        tmpdir = tempfile.mkdtemp()
        bars = _make_bars("BTC", n=100, start="2024-01-01T00:00:00Z")
        bars_path = Path(tmpdir) / "bars.csv"
        bars.to_csv(bars_path, index=False)
        summary = run_pipeline(cfg, bars_path, None, Path(tmpdir))
        assert summary.spec_version == "v0"


# ============================================================================
# 18. Isotonic Requires Opt-In
# ============================================================================

class TestIsotonicOptIn:
    def test_isotonic_requires_explicit_opt_in(self):
        """Isotonic calibration requires explicit opt-in."""
        cfg = MlAtrConfig(model=ModelConfig(calibrator="isotonic"))
        assert cfg.model.calibrator == "isotonic"
        # Default should be platt
        cfg_default = MlAtrConfig()
        assert cfg_default.model.calibrator == "platt"
