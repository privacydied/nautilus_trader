"""
Hyperliquid BTC/ETH ML+ATR v0 — Offline-Only Research Diagnostic
================================================================

Archive-only ML strategy diagnostic for Hyperliquid BTC/ETH perps.
NOT live trading. NOT paper execution. NOT bot authorization.
Produces reproducible backtest artifacts and tests only.

Status taxonomy (all allowed):
  ML_ATR_V0_BACKTEST_READY                       — transient/internal only, never in completed summary
  ML_ATR_V0_NEEDS_MORE_DATA
  ML_ATR_V0_VALIDATION_CALIBRATION_FAILED
  ML_ATR_V0_INSUFFICIENT_TEST_TRADES
  ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED
  ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE
  ML_ATR_V0_ERROR_INVALID_INPUT
  ML_ATR_V0_ERROR_LOOKAHEAD_AUDIT_FAILED

Forbidden statuses (must never appear in output):
  TRADE_READY, EXECUTION_READY, LIVE_READY, CANDIDATE_FOR_LIVE, PROFITABLE
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SYMBOLS = frozenset({"BTC", "ETH"})
SPEC_VERSION = "v0"
STUDY_ID = "hyperliquid_btc_eth_ml_atr_v0"

ALLOWED_STATUSES = {
    "ML_ATR_V0_BACKTEST_READY",
    "ML_ATR_V0_NEEDS_MORE_DATA",
    "ML_ATR_V0_VALIDATION_CALIBRATION_FAILED",
    "ML_ATR_V0_INSUFFICIENT_TEST_TRADES",
    "ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED",
    "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE",
    "ML_ATR_V0_ERROR_INVALID_INPUT",
    "ML_ATR_V0_ERROR_LOOKAHEAD_AUDIT_FAILED",
}

FORBIDDEN_STATUSES = {"TRADE_READY", "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE", "PROFITABLE"}

TRADES_CSV_COLUMNS = [
    "symbol", "side", "signal_timestamp", "entry_timestamp", "exit_timestamp",
    "entry_price", "exit_price", "entry_atr", "initial_stop", "final_stop",
    "exit_reason", "hold_bars", "funding_periods_held",
    "gross_return_bps", "funding_bps", "fee_slippage_bps", "net_return_bps",
    "calibrated_p_up_at_signal",
]

FEATURE_COEF_CSV_COLUMNS = [
    "feature_name", "raw_coefficient", "scaler_mean", "scaler_scale", "standardized_coefficient",
]

RELIABILITY_CSV_COLUMNS = [
    "bin_low", "bin_high", "count", "mean_predicted_probability",
    "empirical_win_rate", "absolute_calibration_error",
]

CSV_FLOAT_FMT = "%.8f"


# ---------------------------------------------------------------------------
# Frozen Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SplitConfig:
    train_start: str = "2024-01-01T00:00:00Z"
    train_end: str = "2024-12-31T23:59:59Z"
    validation_start: str = "2025-01-01T00:00:00Z"
    validation_end: str = "2025-06-30T23:59:59Z"
    test_start: str = "2025-07-01T00:00:00Z"
    min_train_rows: int = 2000
    min_validation_rows: int = 500
    min_test_rows: int = 500


@dataclass(frozen=True)
class FeatureConfig:
    label_horizon_bars: int = 24
    atr_lookback: int = 14
    feature_names: Tuple[str, ...] = (
        "ret_1h", "ret_4h", "ret_24h", "realized_vol_24h",
        "atr_norm_14h", "funding_current", "funding_mean_24h", "rsi_14h",
    )


@dataclass(frozen=True)
class CostConfig:
    fee_bps_per_side: float = 1.0
    slippage_bps_per_side: float = 0.5
    funding_interval_hours: int = 1
    max_abs_funding_rate: float = 0.01
    allow_zero_volume_bars: bool = True
    # Reference Hyperliquid taker rates as of 2025-01 (bps per side)
    reference_taker_fee_bps: float = 1.0
    reference_maker_fee_bps: float = 0.0


@dataclass(frozen=True)
class ExitConfig:
    stop_atr_mult: float = 2.0
    trailing_atr_mult: float = 3.0


@dataclass(frozen=True)
class ModelConfig:
    long_threshold: float = 0.55
    short_threshold: float = 0.40
    model_backend: str = "auto"  # sklearn, numpy_fallback, auto
    calibrator: str = "platt"   # platt, isotonic
    C: float = 1.0
    seed: int = 42
    reliability_bins: int = 10
    min_test_trades: int = 50
    min_test_long_trades: int = 10
    min_test_short_trades: int = 10
    max_gap_hours: int = 24
    strict_funding: bool = False
    no_funding: bool = False
    csv_float_format: str = CSV_FLOAT_FMT


@dataclass(frozen=True)
class MlAtrConfig:
    split: SplitConfig = field(default_factory=SplitConfig)
    feature: FeatureConfig = field(default_factory=FeatureConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    symbols: Tuple[str, ...] = ("BTC", "ETH")
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Result Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class BacktestTrade:
    symbol: str
    side: str  # long / short
    signal_timestamp: pd.Timestamp
    entry_timestamp: pd.Timestamp
    exit_timestamp: pd.Timestamp
    entry_price: float
    exit_price: float
    entry_atr: float
    initial_stop: float
    final_stop: float
    exit_reason: str  # initial_stop, trailing_stop, initial_stop_at_entry, end_of_data
    hold_bars: int
    funding_periods_held: int
    gross_return_bps: float
    funding_bps: float
    fee_slippage_bps: float
    net_return_bps: float
    calibrated_p_up_at_signal: float


@dataclass
class CalibrationSummary:
    method: str  # platt / isotonic
    a: Optional[float] = None
    b: Optional[float] = None
    validation_brier: float = 0.0
    validation_ece: float = 0.0
    validation_mce: float = 0.0
    test_brier: float = 0.0
    test_ece: float = 0.0
    test_mce: float = 0.0
    validation_reliability: List[Dict[str, Any]] = field(default_factory=list)
    test_reliability: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class BacktestSummary:
    total_trades: int = 0
    long_trades: int = 0
    short_trades: int = 0
    mean_gross_bps: float = 0.0
    median_gross_bps: float = 0.0
    mean_net_bps: float = 0.0
    median_net_bps: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_bps: float = 0.0
    avg_hold_bars: float = 0.0
    funding_contribution_bps: float = 0.0
    fee_slippage_contribution_bps: float = 0.0
    mean_funding_bps_per_trade: float = 0.0
    mean_fee_slippage_bps_per_trade: float = 0.0
    trades_closed_at_end_of_data: int = 0
    sharpe: float = 0.0
    sortino: float = 0.0
    bootstrap_ci_lower: float = 0.0
    bootstrap_ci_upper: float = 0.0
    per_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class RunSummary:
    status: str
    reason: str = ""
    study_id: str = STUDY_ID
    spec_version: str = SPEC_VERSION
    run_id: str = ""
    config_hash: str = ""
    train_rows: int = 0
    validation_rows: int = 0
    test_rows: int = 0
    train_positive_pct: float = 0.0
    validation_positive_pct: float = 0.0
    test_positive_pct: float = 0.0
    label_tie_count: Dict[str, int] = field(default_factory=dict)
    feature_drop_count: Dict[str, int] = field(default_factory=dict)
    train_auc: float = 0.0
    validation_auc: float = 0.0
    validation_metrics: Optional[BacktestSummary] = None
    test_metrics: Optional[BacktestSummary] = None
    calibration: Optional[CalibrationSummary] = None
    warnings: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)


# ===========================================================================
# Data Loading
# ===========================================================================
def _detect_timestamp_dtype(series: pd.Series) -> str:
    """Return a string describing the dtype family of the timestamp series."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime64"
    if pd.api.types.is_integer_dtype(series):
        return "int_epoch"
    if pd.api.types.is_float_dtype(series):
        return "float_epoch"
    return "string"


def _ensure_utc_aware(ts_series: pd.Series) -> pd.Series:
    """Convert timestamp series to UTC-aware datetime. Fail if naive."""
    if pd.api.types.is_datetime64_any_dtype(ts_series):
        if ts_series.dt.tz is None:
            raise ValueError(
                "Naive timestamps detected. All timestamps must be timezone-aware UTC. "
                "Do not silently assume UTC for naive timestamps."
            )
        return ts_series.dt.tz_convert("UTC")
    # Epoch: assume seconds if < 1e12, else milliseconds
    if pd.api.types.is_integer_dtype(ts_series) or pd.api.types.is_float_dtype(ts_series):
        vals = ts_series.astype(float)
        if vals.median() > 1e12:
            return pd.to_datetime(vals, unit="ms", utc=True)
        return pd.to_datetime(vals, unit="s", utc=True)
    # String
    parsed = pd.to_datetime(ts_series, utc=True, format="mixed")
    if parsed.dt.tz is None:
        raise ValueError("Parsed timestamps are naive — expected UTC-aware.")
    return parsed


def load_bars(path: Path, symbols: Tuple[str, ...]) -> pd.DataFrame:
    """Load bar CSV or Parquet. Returns DataFrame sorted by symbol, timestamp."""
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Bar file missing columns: {missing}")
    # Symbol check
    unknown = set(df["symbol"].unique()) - VALID_SYMBOLS
    if unknown:
        raise ValueError(f"Unknown symbols in bar input: {unknown}. Only {VALID_SYMBOLS} are valid for v0.")
    # Timestamp
    ts_dtype = _detect_timestamp_dtype(df["timestamp"])
    df["timestamp"] = _ensure_utc_aware(df["timestamp"])
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    df.attrs["timestamp_dtype"] = ts_dtype
    return df


def load_funding(path: Path) -> pd.DataFrame:
    """Load funding CSV or Parquet."""
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    required = {"timestamp", "symbol", "funding_rate"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Funding file missing columns: {missing}")
    unknown = set(df["symbol"].unique()) - VALID_SYMBOLS
    if unknown:
        raise ValueError(f"Unknown symbols in funding input: {unknown}.")
    ts_dtype = _detect_timestamp_dtype(df["timestamp"])
    df["timestamp"] = _ensure_utc_aware(df["timestamp"])
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    df.attrs["timestamp_dtype"] = ts_dtype
    return df


# ===========================================================================
# Data Validation
# ===========================================================================
def validate_ohlc(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """OHLC sanity check. Returns (clean_df, stats)."""
    n_before = len(df)
    mask = (
        (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
        | (df["open"] <= 0) | (df["high"] <= 0) | (df["low"] <= 0) | (df["close"] <= 0)
        | df["open"].isna() | df["high"].isna() | df["low"].isna() | df["close"].isna()
        | (df["volume"] < 0) | df["volume"].isna()
    )
    rejects = int(mask.sum())
    reject_rate = rejects / n_before if n_before > 0 else 0.0
    zero_vol = int((df["volume"] == 0).sum())
    clean = df[~mask].copy()
    stats = {
        "ohlc_sanity_rejects": rejects,
        "ohlc_sanity_reject_rate": reject_rate,
        "zero_volume_bar_count": zero_vol,
    }
    return clean, stats


def validate_timestamp_alignment(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Check bars are aligned to UTC hour boundaries."""
    ts = df["timestamp"]
    if hasattr(ts.dt, "minute"):
        misaligned = (ts.dt.minute != 0) | (ts.dt.second != 0) | (ts.dt.microsecond != 0)
    else:
        misaligned = ts != ts.dt.floor("h")
    n_misaligned = int(misaligned.sum())
    n_total = len(df)
    misaligned_rate = n_misaligned / n_total if n_total > 0 else 0.0
    return df, {
        "timestamp_misaligned_count": n_misaligned,
        "timestamp_misaligned_rate": misaligned_rate,
    }


def detect_gaps(df: pd.DataFrame, max_gap_hours: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Detect missing 1h bars per symbol. Returns (gap_list, {symbol: gap_hours})."""
    gaps = []
    gap_hours_by_symbol: Dict[str, int] = {}
    for sym in sorted(df["symbol"].unique()):
        sub = df[df["symbol"] == sym].sort_values("timestamp")
        if len(sub) < 2:
            continue
        ts = sub["timestamp"].values
        diffs = np.diff(ts).astype("timedelta64[h]").astype(int)
        gap_mask = diffs > 1
        for idx in np.where(gap_mask)[0]:
            gap_start = pd.Timestamp(ts[idx]) + pd.Timedelta(hours=1)
            gap_end = pd.Timestamp(ts[idx + 1]) - pd.Timedelta(hours=1)
            gap_h = int(diffs[idx]) - 1
            gaps.append({
                "symbol": sym,
                "gap_start": str(gap_start),
                "gap_end": str(gap_end),
                "gap_hours": gap_h,
            })
        total_gap = sum(g["gap_hours"] for g in gaps if g["symbol"] == sym)
        gap_hours_by_symbol[sym] = total_gap
    return gaps, gap_hours_by_symbol


def validate_funding(
    funding_df: pd.DataFrame,
    cost_cfg: CostConfig,
    no_funding: bool,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Funding sanity checks."""
    if no_funding or funding_df is None or len(funding_df) == 0:
        return funding_df, {"funding_sanity_rejects": 0}
    n_before = len(funding_df)
    mask = funding_df["funding_rate"].abs() > cost_cfg.max_abs_funding_rate
    rejects = int(mask.sum())
    clean = funding_df[~mask].copy()
    return clean, {"funding_sanity_rejects": rejects}


# ===========================================================================
# Feature Generation
# ===========================================================================
def compute_rsi_wilder(closes: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI using Wilder's smoothing.

    Algorithm:
    1. Compute price changes: delta = close[t] - close[t-1]
    2. Separate gains and losses
    3. Seed: simple mean of first `period` gains and losses
    4. Wilder smoothing: avg_gain[t] = (avg_gain[t-1] * (period-1) + gain[t]) / period
    5. RS = avg_gain / avg_loss; RSI = 100 - 100/(1+RS)
    """
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = pd.Series(np.nan, index=closes.index, dtype=float)
    avg_loss = pd.Series(np.nan, index=closes.index, dtype=float)

    # Seed with simple mean of first `period` changes
    first_valid = period
    if len(gain.dropna()) < period:
        return pd.Series(50.0, index=closes.index, dtype=float)

    avg_gain.iloc[first_valid] = gain.iloc[1:first_valid + 1].mean()
    avg_loss.iloc[first_valid] = loss.iloc[1:first_valid + 1].mean()

    for i in range(first_valid + 1, len(closes)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.clip(0, 100)
    return rsi


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR using Wilder's smoothing (exponential with alpha=1/period)."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def generate_features(
    bars: pd.DataFrame,
    funding_df: Optional[pd.DataFrame],
    feature_cfg: FeatureConfig,
    cost_cfg: CostConfig,
    no_funding: bool,
) -> pd.DataFrame:
    """
    Generate features for a single symbol's bars.
    All features are past-only: feature[t] uses only bars with timestamp <= t.
    """
    df = bars.copy()
    df = df.sort_values("timestamp").reset_index(drop=True)
    close = df["close"]

    # Log returns
    log_close = np.log(close)
    df["ret_1h"] = log_close - log_close.shift(1)
    df["ret_4h"] = log_close - log_close.shift(4)
    df["ret_24h"] = log_close - log_close.shift(24)

    # Realized vol
    df["realized_vol_24h"] = df["ret_1h"].rolling(window=24, min_periods=24).std()

    # ATR normalized
    atr = compute_atr(df["high"], df["low"], df["close"], period=feature_cfg.atr_lookback)
    df["atr_14h_raw"] = atr
    df["atr_norm_14h"] = atr / df["close"]

    # RSI
    df["rsi_14h"] = compute_rsi_wilder(close, period=14)

    # Funding features
    if not no_funding and funding_df is not None and len(funding_df) > 0:
        sym = df["symbol"].iloc[0]
        fsub = funding_df[funding_df["symbol"] == sym].sort_values("timestamp").copy()
        if len(fsub) > 0:
            fsub_idx = fsub.set_index("timestamp")["funding_rate"]
            # For each bar close, find most recent funding <= t_close - 1ns
            bar_times = df["timestamp"]
            fc_vals = []
            for bt in bar_times:
                cutoff = bt - pd.Timedelta(nanoseconds=1)
                valid = fsub_idx[fsub_idx.index <= cutoff]
                if len(valid) > 0:
                    fc_vals.append(float(valid.iloc[-1]))
                else:
                    fc_vals.append(0.0)
            df["funding_current"] = fc_vals

            # funding_mean_24h: rolling mean over last 24 funding rows
            fm_vals = []
            for bt in bar_times:
                cutoff = bt - pd.Timedelta(nanoseconds=1)
                valid = fsub_idx[fsub_idx.index <= cutoff]
                if len(valid) >= 24:
                    fm_vals.append(float(valid.iloc[-24:].mean()))
                elif len(valid) > 0:
                    fm_vals.append(float(valid.mean()))
                else:
                    fm_vals.append(0.0)
            df["funding_mean_24h"] = fm_vals
        else:
            df["funding_current"] = 0.0
            df["funding_mean_24h"] = 0.0
    else:
        df["funding_current"] = 0.0
        df["funding_mean_24h"] = 0.0

    return df


def generate_labels(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Generate binary labels. label_up = close[t+horizon] > close[t]."""
    df = df.copy()
    df["future_close"] = df["close"].shift(-horizon)
    df["label_up"] = (df["future_close"] > df["close"]).astype(int)
    # Tie: if future_close == close exactly, label = 0 (already 0 from strict >)
    df["is_tie"] = (df["future_close"] == df["close"]).astype(int)
    df = df.drop(columns=["future_close"])
    return df


# ===========================================================================
# Split Handling
# ===========================================================================
def assign_splits(
    df: pd.DataFrame, split_cfg: SplitConfig, feature_cfg: FeatureConfig
) -> Dict[str, pd.DataFrame]:
    """Assign rows to train/validation/test. Drop NaN features/labels."""
    train_start = pd.Timestamp(split_cfg.train_start, tz="UTC")
    train_end = pd.Timestamp(split_cfg.train_end, tz="UTC")
    val_start = pd.Timestamp(split_cfg.validation_start, tz="UTC")
    val_end = pd.Timestamp(split_cfg.validation_end, tz="UTC")
    test_start = pd.Timestamp(split_cfg.test_start, tz="UTC")

    # Drop rows with NaN in any feature column or label
    feature_cols = list(feature_cfg.feature_names)
    drop_cols = feature_cols + ["label_up"]
    df_clean = df.dropna(subset=drop_cols).copy()

    splits = {}
    splits["train"] = df_clean[
        (df_clean["timestamp"] >= train_start) & (df_clean["timestamp"] <= train_end)
    ].copy()
    splits["validation"] = df_clean[
        (df_clean["timestamp"] >= val_start) & (df_clean["timestamp"] <= val_end)
    ].copy()
    splits["test"] = df_clean[df_clean["timestamp"] >= test_start].copy()

    # No-overlap audit
    if len(splits["train"]) > 0 and len(splits["validation"]) > 0:
        assert splits["train"]["timestamp"].max() < splits["validation"]["timestamp"].min(), (
            "Lookahead audit failed: train/val overlap"
        )
    if len(splits["validation"]) > 0 and len(splits["test"]) > 0:
        assert splits["validation"]["timestamp"].max() < splits["test"]["timestamp"].min(), (
            "Lookahead audit failed: val/test overlap"
        )

    return splits


# ===========================================================================
# Model: Sklearn Backend
# ===========================================================================
def _train_sklearn(
    X_train: np.ndarray, y_train: np.ndarray, C: float, seed: int
) -> Tuple[np.ndarray, float]:
    """Train logistic regression with sklearn. Returns (coef, intercept)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)
    model = LogisticRegression(C=C, solver="lbfgs", max_iter=1000, random_state=seed)
    model.fit(X_scaled, y_train)
    return model.coef_[0], model.intercept_[0], scaler


def _predict_sklearn(coef: np.ndarray, intercept: float, scaler, X: np.ndarray) -> np.ndarray:
    """Predict probabilities with sklearn model."""
    X_scaled = scaler.transform(X)
    z = X_scaled @ coef + intercept
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


# ===========================================================================
# Model: Numpy Fallback Backend
# ===========================================================================
def _train_numpy_fallback(
    X_train: np.ndarray, y_train: np.ndarray, C: float, seed: int
) -> Tuple[np.ndarray, float, str]:
    """
    Deterministic logistic regression via gradient descent.
    Returns (coef, intercept, solver_name).

    Uses manual standardization (no sklearn dependency for numpy fallback).
    """
    n_features = X_train.shape[1]

    # Manual standardization (no sklearn dependency)
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std == 0] = 1.0
    X_scaled = (X_train - mean) / std

    rng = np.random.RandomState(seed)
    coef = rng.normal(0, 0.01, size=n_features)
    intercept = 0.0
    lr = 0.01
    n_iter = 2000

    for _ in range(n_iter):
        z = X_scaled @ coef + intercept
        z = np.clip(z, -500, 500)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = X_scaled.T @ (p - y_train) / len(y_train) + (1.0 / C) * coef / len(y_train)
        grad_intercept = np.mean(p - y_train)
        coef -= lr * grad
        intercept -= lr * grad_intercept

    return coef, intercept, "deterministic_gradient_descent_2000iter"


def _predict_numpy(coef: np.ndarray, intercept: float, X: np.ndarray) -> np.ndarray:
    """Predict probabilities with numpy model."""
    z = X @ coef + intercept
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


# ===========================================================================
# Calibration
# ===========================================================================
def calibrate_platt(probs: np.ndarray, labels: np.ndarray) -> Tuple[float, float]:
    """
    Platt scaling on validation data.
    Minimizes: min_a,b sum [t_i * log(s(f_i)) + (1-t_i) * log(1-s(f_i))]
    where s(f) = 1/(1+exp(af+b)), t_i = (y_i+1)/(2+2) mapped from {0,1}.
    Uses Newton's method, deterministic.
    """
    y = labels.astype(float)
    # Map to {0.1, 0.9} for numerical stability
    t = np.where(y == 1, 0.9, 0.1)
    f = probs.copy()
    # Avoid exact 0/1
    f = np.clip(f, 1e-7, 1 - 1e-7)
    a, b = 0.0, np.log((1 - 0.1) / 0.1)  # initial b from prior

    for _ in range(100):
        af = a * f + b
        af = np.clip(af, -500, 500)
        p = 1.0 / (1.0 + np.exp(af))
        # Gradient
        dpda = (t - 1 + p) * f
        dpdb = t - 1 + p
        d2a = -p * (1 - p) * f * f
        d2b = -p * (1 - p)
        d2ab = -p * (1 - p) * f

        g = np.array([dpda.sum(), dpdb.sum()])
        H = np.array([[d2a.sum(), d2ab.sum()], [d2ab.sum(), d2b.sum()]])

        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        a += step[0]
        b += step[1]
        if np.max(np.abs(step)) < 1e-10:
            break

    return float(a), float(b)


def apply_platt(a: float, b: float, probs: np.ndarray) -> np.ndarray:
    """Apply Platt scaling: s(f) = 1/(1+exp(af+b))."""
    z = a * probs + b
    z = np.clip(z, -500, 500)
    return 1.0 / (1.0 + np.exp(z))


def compute_reliability(
    probs: np.ndarray, labels: np.ndarray, n_bins: int
) -> Tuple[List[Dict[str, Any]], float, float, List[str]]:
    """Compute reliability diagram bins, ECE, MCE."""
    bins = np.linspace(0, 1, n_bins + 1)
    table = []
    warnings_list = []
    total_count = len(probs)
    ece = 0.0
    mce = 0.0

    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        if i == n_bins - 1:
            mask = (probs >= lo) & (probs <= hi)
        else:
            mask = (probs >= lo) & (probs < hi)
        count = int(mask.sum())
        if count == 0:
            table.append({
                "bin_low": float(lo), "bin_high": float(hi), "count": 0,
                "mean_predicted_probability": 0.0, "empirical_win_rate": 0.0,
                "absolute_calibration_error": 0.0,
            })
            continue
        mean_pred = float(probs[mask].mean())
        emp_rate = float(labels[mask].mean())
        ace = abs(mean_pred - emp_rate)
        ece += count / total_count * ace
        mce = max(mce, ace)
        if count < 10:
            warnings_list.append("sparse_calibration_bins")
        table.append({
            "bin_low": float(lo), "bin_high": float(hi), "count": count,
            "mean_predicted_probability": mean_pred, "empirical_win_rate": emp_rate,
            "absolute_calibration_error": ace,
        })

    return table, float(ece), float(mce), warnings_list


def compute_brier(probs: np.ndarray, labels: np.ndarray) -> float:
    """Brier score."""
    return float(np.mean((probs - labels) ** 2))


# ===========================================================================
# Backtest Engine
# ===========================================================================
def _compute_funding_bps(
    side: str,
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    funding_df: Optional[pd.DataFrame],
    entry_price: float,
    cost_cfg: CostConfig,
    strict: bool,
) -> Tuple[float, int, List[str]]:
    """
    Compute total funding cost in bps for a trade.
    Long pays positive funding, receives negative.
    Short receives positive funding, pays negative.
    """
    warnings_list = []
    if funding_df is None or len(funding_df) == 0:
        return 0.0, 0, warnings_list

    sym = None  # caller should filter
    mask = (funding_df["timestamp"] > entry_ts) & (funding_df["timestamp"] <= exit_ts)
    relevant = funding_df[mask]
    n_periods = len(relevant)
    total_rate = relevant["funding_rate"].sum()

    # Sign: long pays positive, short pays negative
    if side == "long":
        funding_bps = -total_rate * 10000  # positive rate -> negative return
    else:
        funding_bps = total_rate * 10000   # positive rate -> positive return (short receives)

    return float(funding_bps), n_periods, warnings_list


def run_backtest(
    signal_df: pd.DataFrame,
    exit_cfg: ExitConfig,
    cost_cfg: CostConfig,
    long_threshold: float,
    short_threshold: float,
    funding_df: Optional[pd.DataFrame],
    strict_funding: bool,
) -> List[BacktestTrade]:
    """
    Backtest calibrated signals on a split.
    signal_df must have: timestamp, open, high, low, close, atr_14h_raw, calibrated_p_up.
    """
    trades: List[BacktestTrade] = []
    df = signal_df.sort_values("timestamp").reset_index(drop=True)
    n = len(df)

    i = 0
    while i < n - 1:
        row = df.iloc[i]
        p_up = row["calibrated_p_up"]
        signal_ts = row["timestamp"]

        if p_up >= long_threshold:
            side = "long"
        elif p_up <= short_threshold:
            side = "short"
        else:
            i += 1
            continue

        # Entry at next bar open
        if i + 1 >= n:
            break
        entry_row = df.iloc[i + 1]
        entry_price = float(entry_row["open"])
        entry_ts = entry_row["timestamp"]
        atr_at_signal = float(row["atr_14h_raw"])

        if np.isnan(atr_at_signal) or atr_at_signal <= 0:
            i += 1
            continue

        # Compute stops
        if side == "long":
            initial_stop = entry_price - exit_cfg.stop_atr_mult * atr_at_signal
            trailing_stop = entry_price - exit_cfg.trailing_atr_mult * atr_at_signal
            best_close = entry_price
        else:
            initial_stop = entry_price + exit_cfg.stop_atr_mult * atr_at_signal
            trailing_stop = entry_price + exit_cfg.trailing_atr_mult * atr_at_signal
            best_close = entry_price

        active_stop = initial_stop
        is_entry_bar = True

        # Walk forward through bars
        j = i + 1
        exit_price = entry_price
        exit_ts = entry_ts
        exit_reason = "end_of_data"
        final_stop = active_stop

        while j < n:
            bar = df.iloc[j]
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_open = float(bar["open"])
            bar_close = float(bar["close"])

            if side == "long":
                # Check stop hit
                if bar_low <= active_stop:
                    # Conservative fill: worse price
                    fill_price = min(bar_open, active_stop)
                    if is_entry_bar:
                        exit_reason = "initial_stop_at_entry"
                    elif active_stop == initial_stop:
                        exit_reason = "initial_stop"
                    else:
                        exit_reason = "trailing_stop"
                    exit_price = fill_price
                    exit_ts = bar["timestamp"]
                    final_stop = active_stop
                    break

                # Update trailing on prior bar close (not current bar)
                if not is_entry_bar:
                    best_close = max(best_close, bar_close)
                    new_trailing = best_close - exit_cfg.trailing_atr_mult * atr_at_signal
                    # Trailing only ratchets up (never down)
                    if new_trailing > trailing_stop:
                        trailing_stop = new_trailing
                    active_stop = max(initial_stop, trailing_stop)
                    final_stop = active_stop
                else:
                    is_entry_bar = False
                    best_close = max(best_close, bar_close)
                    final_stop = active_stop

            else:  # short
                if bar_high >= active_stop:
                    fill_price = max(bar_open, active_stop)
                    if is_entry_bar:
                        exit_reason = "initial_stop_at_entry"
                    elif active_stop == initial_stop:
                        exit_reason = "initial_stop"
                    else:
                        exit_reason = "trailing_stop"
                    exit_price = fill_price
                    exit_ts = bar["timestamp"]
                    final_stop = active_stop
                    break

                if not is_entry_bar:
                    best_close = min(best_close, bar_close)
                    new_trailing = best_close + exit_cfg.trailing_atr_mult * atr_at_signal
                    if new_trailing < trailing_stop:
                        trailing_stop = new_trailing
                    active_stop = min(initial_stop, trailing_stop)
                    final_stop = active_stop
                else:
                    is_entry_bar = False
                    best_close = min(best_close, bar_close)
                    final_stop = active_stop

            j += 1

        # End-of-data close
        if j >= n:
            exit_price = float(df.iloc[n - 1]["close"])
            exit_ts = df.iloc[n - 1]["timestamp"]
            exit_reason = "end_of_data"
            final_stop = active_stop

        # Compute returns
        side_sign = 1.0 if side == "long" else -1.0
        gross_bps = ((exit_price - entry_price) / entry_price) * 10000.0 * side_sign
        fee_slippage = 2.0 * (cost_cfg.fee_bps_per_side + cost_cfg.slippage_bps_per_side)

        # Funding
        funding_bps, funding_periods, fund_warns = _compute_funding_bps(
            side, entry_ts, exit_ts, funding_df, entry_price, cost_cfg, strict_funding
        )

        net_bps = gross_bps + funding_bps - fee_slippage

        trade = BacktestTrade(
            symbol=str(df.iloc[i]["symbol"]),
            side=side,
            signal_timestamp=signal_ts,
            entry_timestamp=entry_ts,
            exit_timestamp=exit_ts,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_atr=atr_at_signal,
            initial_stop=initial_stop,
            final_stop=final_stop,
            exit_reason=exit_reason,
            hold_bars=j - (i + 1),
            funding_periods_held=funding_periods,
            gross_return_bps=float(gross_bps),
            funding_bps=float(funding_bps),
            fee_slippage_bps=float(fee_slippage),
            net_return_bps=float(net_bps),
            calibrated_p_up_at_signal=float(p_up),
        )
        trades.append(trade)
        i = j  # skip to bar after exit

    return trades


# ===========================================================================
# Metrics
# ===========================================================================
def compute_trade_metrics(trades: List[BacktestTrade], seed: int) -> BacktestSummary:
    """Compute summary metrics from a list of trades."""
    if not trades:
        return BacktestSummary()

    df = pd.DataFrame([asdict(t) for t in trades])
    n = len(df)
    longs = df[df["side"] == "long"]
    shorts = df[df["side"] == "short"]

    gross = df["gross_return_bps"].values
    net = df["net_return_bps"].values
    wins = (net > 0).sum()
    losses = (net < 0).sum()

    # Win rate: exclude end_of_data from denominator
    non_eod = df[df["exit_reason"] != "end_of_data"]
    if len(non_eod) > 0:
        win_rate = float((non_eod["net_return_bps"] > 0).sum() / len(non_eod))
    else:
        win_rate = 0.0

    # Profit factor
    pos_sum = float(gross[gross > 0].sum()) if (gross > 0).any() else 0.0
    neg_sum = float(abs(gross[gross < 0]).sum()) if (gross < 0).any() else 1e-10
    profit_factor = pos_sum / neg_sum

    # Max drawdown on sequential equity
    equity = np.cumsum(net)
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity - running_max
    max_dd = float(drawdowns.min())

    # Sharpe / Sortino on per-trade returns
    if len(net) > 1 and np.std(net) > 0:
        sharpe = float(np.mean(net) / np.std(net) * np.sqrt(252))
    else:
        sharpe = 0.0

    downside = net[net < 0]
    if len(downside) > 1 and np.std(downside) > 0:
        sortino = float(np.mean(net) / np.std(downside) * np.sqrt(252))
    else:
        sortino = 0.0

    # Bootstrap CI on mean net bps
    rng = np.random.RandomState(seed)
    boot_means = []
    for _ in range(1000):
        sample = rng.choice(net, size=len(net), replace=True)
        boot_means.append(float(np.mean(sample)))
    boot_means = np.array(boot_means)
    ci_lower = float(np.percentile(boot_means, 2.5))
    ci_upper = float(np.percentile(boot_means, 97.5))

    eod_count = int((df["exit_reason"] == "end_of_data").sum())

    # Per-symbol
    per_sym = {}
    for sym in df["symbol"].unique():
        sdf = df[df["symbol"] == sym]
        per_sym[str(sym)] = {
            "total_trades": int(len(sdf)),
            "mean_net_bps": float(sdf["net_return_bps"].mean()),
            "win_rate": float((sdf["net_return_bps"] > 0).mean()),
        }

    return BacktestSummary(
        total_trades=n,
        long_trades=int(len(longs)),
        short_trades=int(len(shorts)),
        mean_gross_bps=float(np.mean(gross)),
        median_gross_bps=float(np.median(gross)),
        mean_net_bps=float(np.mean(net)),
        median_net_bps=float(np.median(net)),
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown_bps=max_dd,
        avg_hold_bars=float(df["hold_bars"].mean()),
        funding_contribution_bps=float(df["funding_bps"].sum()),
        fee_slippage_contribution_bps=float(df["fee_slippage_bps"].sum()),
        mean_funding_bps_per_trade=float(df["funding_bps"].mean()),
        mean_fee_slippage_bps_per_trade=float(df["fee_slippage_bps"].mean()),
        trades_closed_at_end_of_data=eod_count,
        sharpe=sharpe,
        sortino=sortino,
        bootstrap_ci_lower=ci_lower,
        bootstrap_ci_upper=ci_upper,
        per_symbol=per_sym,
    )


# ===========================================================================
# AUC (pure numpy, no sklearn dependency)
# ===========================================================================
def _compute_auc(probs: np.ndarray, labels: np.ndarray) -> float:
    """Compute AUC using the trapezoidal rule. Pure numpy."""
    pos = probs[labels == 1]
    neg = probs[labels == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # Mann-Whitney U
    n_pos = len(pos)
    n_neg = len(neg)
    all_vals = np.concatenate([pos, neg])
    all_labels = np.concatenate([np.ones(n_pos), np.zeros(n_neg)])
    order = np.argsort(all_vals)
    ranked = all_labels[order]
    rank_sum = np.sum(np.where(ranked == 1, np.arange(1, len(ranked) + 1), 0))
    u = rank_sum - n_pos * (n_pos + 1) / 2
    return float(u / (n_pos * n_neg))


# ===========================================================================
# Artifact Writing
# ===========================================================================
def _config_hash(cfg: MlAtrConfig) -> str:
    """Deterministic hash of config."""
    raw = json.dumps(asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _file_hash(path: Path) -> str:
    """SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_trades_csv(trades: List[BacktestTrade], path: Path, fmt: str = CSV_FLOAT_FMT):
    """Write trades.csv with frozen column ordering."""
    if not trades:
        pd.DataFrame(columns=TRADES_CSV_COLUMNS).to_csv(path, index=False, float_format=fmt)
        return
    rows = [asdict(t) for t in trades]
    df = pd.DataFrame(rows)[TRADES_CSV_COLUMNS]
    df.to_csv(path, index=False, float_format=fmt)


def write_feature_coefficients(
    feature_names: List[str],
    coefs: np.ndarray,
    scaler_mean: np.ndarray,
    scaler_scale: np.ndarray,
    path: Path,
    fmt: str = CSV_FLOAT_FMT,
):
    """Write feature_coefficients.csv."""
    std_coefs = coefs * scaler_scale  # standardized = raw * scale
    rows = []
    for i, name in enumerate(feature_names):
        rows.append({
            "feature_name": name,
            "raw_coefficient": coefs[i],
            "scaler_mean": scaler_mean[i],
            "scaler_scale": scaler_scale[i],
            "standardized_coefficient": std_coefs[i],
        })
    pd.DataFrame(rows)[FEATURE_COEF_CSV_COLUMNS].to_csv(path, index=False, float_format=fmt)


def write_reliability_csv(table: List[Dict], path: Path, fmt: str = CSV_FLOAT_FMT):
    """Write reliability CSV."""
    pd.DataFrame(table)[RELIABILITY_CSV_COLUMNS].to_csv(path, index=False, float_format=fmt)


def write_summary_md(
    summary: RunSummary,
    path: Path,
    funding_included: bool,
    min_test_trades: int,
):
    """Write summary.md with DO NOT USE header."""
    lines = [
        "# DO NOT USE FOR LIVE TRADING",
        "",
        "This is an offline research diagnostic output.",
        "Passing v0 does NOT authorize shadow, paper, bot, or live execution.",
        "",
        f"Status: {summary.status}",
    ]
    if summary.reason:
        lines.append(f"Reason: {summary.reason}")
    lines.extend([
        "",
        f"Study: {summary.study_id}",
        f"Spec version: {summary.spec_version}",
        f"Run ID: {summary.run_id}",
        f"Funding: {'included' if funding_included else 'OMITTED (--no-funding)'}",
        "",
        "## Precommitment",
        "See docs/HYPERLIQUID_BTC_ETH_ML_ATR_V0_PRECOMMITMENT.md",
        "",
        "## Non-Goals",
        "- The goal is NOT to find a profitable strategy.",
        "- The goal is to determine whether a simple ML+ATR pipeline produces a calibrated,",
        "  non-leaking diagnostic baseline on Hyperliquid BTC/ETH data.",
        "",
        "## What Passing v0 Means",
        "Passing v0 gates only permits drafting findings and a separate shadow-logging precommitment.",
        "It does NOT unlock paper, live, shadow, or bot execution.",
        "",
        f"Train rows: {summary.train_rows}",
        f"Validation rows: {summary.validation_rows}",
        f"Test rows: {summary.test_rows}",
        f"Train AUC: {summary.train_auc:.4f}",
        f"Validation AUC: {summary.validation_auc:.4f}",
    ])
    if summary.warnings:
        lines.extend(["", "## Warnings"] + [f"- {w}" for w in summary.warnings])
    path.write_text("\n".join(lines) + "\n")


def write_inputs_md(inputs: List[Dict[str, Any]], path: Path):
    """Write INPUTS.md."""
    lines = ["# Input Files", ""]
    for inp in inputs:
        lines.extend([
            f"## {inp['name']}",
            f"- Path: `{inp['path']}`",
            f"- SHA256: `{inp['sha256']}`",
            f"- Rows: {inp['rows']}",
            "",
        ])
    path.write_text("\n".join(lines) + "\n")


def write_model_bundle(
    output_dir: Path,
    cfg: MlAtrConfig,
    coef: np.ndarray,
    intercept: float,
    scaler_mean: np.ndarray,
    scaler_scale: np.ndarray,
    platt_a: Optional[float],
    platt_b: Optional[float],
    summary: RunSummary,
    bars_path: Path,
    funding_path: Optional[Path],
    feature_cols: List[str],
    backend: str,
    timestamp_dtype: str,
    splits: Dict[str, pd.DataFrame],
) -> Path:
    """Export deterministic model bundle for paper runner consumption."""
    import sys

    summary_path = output_dir / "summary.json"
    config_path = output_dir / "config.json"

    summary_sha256 = _file_hash(summary_path) if summary_path.exists() else ""
    config_sha256 = _file_hash(config_path) if config_path.exists() else ""

    # Latest training input timestamp per symbol
    latest_ts = {}
    if "train" in splits and len(splits["train"]) > 0:
        for sym in splits["train"]["symbol"].unique():
            sub = splits["train"][splits["train"]["symbol"] == sym]
            latest_ts[str(sym)] = str(sub["timestamp"].max())

    bundle = {
        "spec_version": "v0",
        "study_id": STUDY_ID,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_id": summary.run_id,
        "source_summary_sha256": summary_sha256,
        "source_config_sha256": config_sha256,
        "source_precommitment_path": "docs/HYPERLIQUID_BTC_ETH_ML_ATR_V0_PRECOMMITMENT.md",
        "source_precommitment_sha256": "",
        "source_summary_status": summary.status,
        "source_test_split_status": "completed" if summary.test_rows > 0 else "pending",
        "source_test_split_boundary_timestamps": {
            "train_start": None,
            "train_end": cfg.split.train_end,
            "validation_start": cfg.split.validation_start,
            "validation_end": cfg.split.validation_end,
            "test_start": cfg.split.test_start,
            "test_end": None,
        },
        "symbols": list(cfg.symbols),
        "feature_names": feature_cols,
        "scaler_mean": scaler_mean.tolist(),
        "scaler_scale": scaler_scale.tolist(),
        "model_backend": backend,
        "logistic_intercept": float(intercept),
        "logistic_coefficients": coef.tolist(),
        "regularization_C": cfg.model.C,
        "calibrator": cfg.model.calibrator,
        "platt_params": {"a": platt_a, "b": platt_b},
        "thresholds": {
            "long_threshold": cfg.model.long_threshold,
            "short_threshold": cfg.model.short_threshold,
        },
        "feature_config": {
            "label_horizon_bars": cfg.feature.label_horizon_bars,
            "atr_lookback": cfg.feature.atr_lookback,
        },
        "exit_config": {
            "stop_atr_mult": cfg.exit.stop_atr_mult,
            "trailing_atr_mult": cfg.exit.trailing_atr_mult,
        },
        "cost_config": {
            "fee_bps_per_side": cfg.cost.fee_bps_per_side,
            "slippage_bps_per_side": cfg.cost.slippage_bps_per_side,
            "funding_interval_hours": cfg.cost.funding_interval_hours,
            "max_abs_funding_rate": cfg.cost.max_abs_funding_rate,
            "allow_zero_volume_bars": cfg.cost.allow_zero_volume_bars,
        },
        "label_horizon_bars": cfg.feature.label_horizon_bars,
        "train_window": {"start": None, "end": cfg.split.train_end},
        "validation_window": {"start": cfg.split.validation_start, "end": cfg.split.validation_end},
        "test_window": {"start": cfg.split.test_start, "end": None},
        "latest_training_input_timestamp_by_symbol": latest_ts,
        "eligibility_status_from_source_summary": summary.status,
        "package_versions": {
            "python_version": sys.version,
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "sklearn_version": getattr(sys.modules.get("sklearn", None), "__version__", "unavailable"),
        },
        "bundle_sha256_self": None,
        "safety": {
            "observer_only": True,
            "no_orders": True,
            "no_auth": True,
            "no_live_execution": True,
        },
    }

    # Compute self-hash
    bundle_for_hash = {k: v for k, v in bundle.items() if k != "bundle_sha256_self"}
    raw = json.dumps(bundle_for_hash, sort_keys=True, indent=2, default=str)
    bundle["bundle_sha256_self"] = hashlib.sha256(raw.encode()).hexdigest()

    bundle_path = output_dir / "model_bundle.json"
    bundle_path.write_text(json.dumps(bundle, sort_keys=True, indent=2, default=str) + "\n")
    return bundle_path


def write_manifest(
    manifest: Dict[str, Any],
    path: Path,
):
    """Write manifest.json."""
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2, default=str) + "\n")


def write_config_json(cfg: MlAtrConfig, path: Path):
    """Write config.json (byte-identical across reruns)."""
    d = asdict(cfg)
    path.write_text(json.dumps(d, sort_keys=True, indent=2, default=str) + "\n")


# ===========================================================================
# Main Pipeline
# ===========================================================================
def run_pipeline(
    cfg: MlAtrConfig,
    bars_path: Path,
    funding_path: Optional[Path],
    output_root: Path,
    symbols: Tuple[str, ...] = ("BTC", "ETH"),
) -> RunSummary:
    """
    Execute the full v0 pipeline. Returns RunSummary.
    """
    import hashlib

    run_id = f"{cfg.model.seed}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = RunSummary(
        status="",
        run_id=run_id,
        config_hash=_config_hash(cfg),
    )

    # --- Load data ---
    bars = load_bars(bars_path, symbols)
    timestamp_dtype = bars.attrs.get("timestamp_dtype", "unknown")
    n_bars_raw = len(bars)

    # Filter to configured symbols
    bars = bars[bars["symbol"].isin(symbols)].copy()

    # OHLC sanity
    bars, ohlc_stats = validate_ohlc(bars)

    # Reject rate gate
    if ohlc_stats["ohlc_sanity_reject_rate"] > 0.001:
        summary.status = "ML_ATR_V0_ERROR_INVALID_INPUT"
        summary.reason = "ohlc_reject_rate_exceeds_tolerance"
        return summary

    # Timestamp alignment
    bars, align_stats = validate_timestamp_alignment(bars)
    if align_stats["timestamp_misaligned_rate"] > 0.001:
        summary.status = "ML_ATR_V0_ERROR_INVALID_INPUT"
        summary.reason = "timestamp_misalignment_exceeds_tolerance"
        return summary

    # Gap detection
    gaps, gap_hours = detect_gaps(bars, cfg.model.max_gap_hours)
    for sym, gh in gap_hours.items():
        if gh > cfg.model.max_gap_hours:
            summary.status = "ML_ATR_V0_ERROR_INVALID_INPUT"
            summary.reason = f"gap_hours_exceeds_max for {sym}"
            return summary

    # Funding
    funding_df = None
    funding_sanity_rejects = 0
    if not cfg.model.no_funding and funding_path is not None:
        funding_df = load_funding(funding_path)
        funding_df, f_stats = validate_funding(funding_df, cfg.cost, cfg.model.no_funding)
        funding_sanity_rejects = f_stats["funding_sanity_rejects"]

    # --- Features per symbol ---
    all_dfs = []
    for sym in sorted(bars["symbol"].unique()):
        sub = bars[bars["symbol"] == sym].copy()
        fdf = generate_features(sub, funding_df, cfg.feature, cfg.cost, cfg.model.no_funding)
        fdf = generate_labels(fdf, cfg.feature.label_horizon_bars)
        all_dfs.append(fdf)

    bars_full = pd.concat(all_dfs, ignore_index=True)

    # --- Splits ---
    try:
        splits = assign_splits(bars_full, cfg.split, cfg.feature)
    except AssertionError as e:
        summary.status = "ML_ATR_V0_ERROR_LOOKAHEAD_AUDIT_FAILED"
        summary.reason = str(e)
        return summary

    # Row count checks
    for split_name, min_rows in [
        ("train", cfg.split.min_train_rows),
        ("validation", cfg.split.min_validation_rows),
        ("test", cfg.split.min_test_rows),
    ]:
        if len(splits[split_name]) < min_rows:
            summary.status = "ML_ATR_V0_NEEDS_MORE_DATA"
            summary.reason = f"{split_name}_has_{len(splits[split_name])}_rows_below_{min_rows}"
            return summary

    # Label balance
    for split_name in ["train", "validation", "test"]:
        s = splits[split_name]
        pos_pct = float(s["label_up"].mean())
        if pos_pct < 0.30 or pos_pct > 0.70:
            summary.status = "ML_ATR_V0_ERROR_INVALID_INPUT"
            summary.reason = f"degenerate_label_balance_in_{split_name}"
            return summary

    summary.train_rows = len(splits["train"])
    summary.validation_rows = len(splits["validation"])
    summary.test_rows = len(splits["test"])
    summary.train_positive_pct = float(splits["train"]["label_up"].mean() * 100)
    summary.validation_positive_pct = float(splits["validation"]["label_up"].mean() * 100)
    summary.test_positive_pct = float(splits["test"]["label_up"].mean() * 100)

    # Feature drop counts per split
    for sn in ["train", "validation", "test"]:
        summary.feature_drop_count[sn] = 0  # already dropped NaN above

    # Label tie counts
    for sn in ["train", "validation", "test"]:
        summary.label_tie_count[sn] = int(splits[sn]["is_tie"].sum()) if "is_tie" in splits[sn].columns else 0

    # --- Model ---
    feature_cols = list(cfg.feature.feature_names)
    X_train = splits["train"][feature_cols].values
    y_train = splits["train"]["label_up"].values
    X_val = splits["validation"][feature_cols].values
    y_val = splits["validation"]["label_up"].values
    X_test = splits["test"][feature_cols].values
    y_test = splits["test"]["label_up"].values

    # Resolve backend
    backend = cfg.model.model_backend
    sklearn_available = False
    try:
        import sklearn  # noqa: F401
        sklearn_available = True
    except ImportError:
        pass

    if backend == "auto":
        backend = "sklearn" if sklearn_available else "numpy_fallback"
    elif backend == "sklearn" and not sklearn_available:
        summary.status = "ML_ATR_V0_ERROR_INVALID_INPUT"
        summary.reason = "sklearn_requested_but_unavailable"
        return summary

    numpy_fallback_solver = None
    scaler_obj = None

    if backend == "sklearn":
        coef, intercept, scaler_obj = _train_sklearn(X_train, y_train, cfg.model.C, cfg.model.seed)
        val_probs = _predict_sklearn(coef, intercept, scaler_obj, X_val)
        test_probs_raw = _predict_sklearn(coef, intercept, scaler_obj, X_test)
        scaler_mean = scaler_obj.mean_
        scaler_scale = scaler_obj.scale_
    else:
        coef, intercept, numpy_fallback_solver = _train_numpy_fallback(X_train, y_train, cfg.model.C, cfg.model.seed)
        # Numpy fallback uses its own scaler
        from sklearn.preprocessing import StandardScaler
        scaler_obj = StandardScaler()
        scaler_obj.fit(X_train)
        scaler_mean = scaler_obj.mean_.copy()
        scaler_scale = scaler_obj.scale_.copy()
        X_val_scaled = scaler_obj.transform(X_val)
        X_test_scaled = scaler_obj.transform(X_test)
        val_probs = _predict_numpy(coef, intercept, X_val_scaled)
        test_probs_raw = _predict_numpy(coef, intercept, X_test_scaled)

    # AUC
    summary.train_auc = _compute_auc(
        _predict_sklearn(coef, intercept, scaler_obj, X_train) if backend == "sklearn"
        else _predict_numpy(coef, intercept, scaler_obj.transform(X_train)),
        y_train
    )
    summary.validation_auc = _compute_auc(val_probs, y_val)

    # Validation AUC gate
    if summary.validation_auc < 0.51:
        summary.status = "ML_ATR_V0_VALIDATION_CALIBRATION_FAILED"
        summary.reason = "validation_auc_below_0.51"
        return summary

    # --- Calibration ---
    if cfg.model.calibrator == "isotonic":
        # Requires explicit opt-in
        from sklearn.isotonic import IsotonicRegression
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(val_probs, y_val)
        cal_val_probs = iso.predict(val_probs)
        cal_test_probs = iso.predict(test_probs_raw)
        cal_summary = CalibrationSummary(method="isotonic")
    else:
        # Platt
        if len(np.unique(y_val)) < 2:
            summary.status = "ML_ATR_V0_VALIDATION_CALIBRATION_FAILED"
            summary.reason = "validation_single_class"
            return summary
        a, b = calibrate_platt(val_probs, y_val)
        cal_val_probs = apply_platt(a, b, val_probs)
        cal_test_probs = apply_platt(a, b, test_probs_raw)
        cal_summary = CalibrationSummary(method="platt", a=a, b=b)

    # Reliability
    val_table, val_ece, val_mce, val_warns = compute_reliability(cal_val_probs, y_val, cfg.model.reliability_bins)
    test_table, test_ece, test_mce, test_warns = compute_reliability(cal_test_probs, y_test, cfg.model.reliability_bins)
    cal_summary.validation_reliability = val_table
    cal_summary.test_reliability = test_table
    cal_summary.validation_brier = compute_brier(cal_val_probs, y_val)
    cal_summary.validation_ece = val_ece
    cal_summary.validation_mce = val_mce
    cal_summary.test_brier = compute_brier(cal_test_probs, y_test)
    cal_summary.test_ece = test_ece
    cal_summary.test_mce = test_mce
    cal_summary.warnings = val_warns + test_warns

    # --- Add calibrated probs to splits ---
    splits["validation"] = splits["validation"].copy()
    splits["validation"]["calibrated_p_up"] = cal_val_probs
    splits["test"] = splits["test"].copy()
    splits["test"]["calibrated_p_up"] = cal_test_probs

    # --- Backtest ---
    # Validation backtest
    val_trades = run_backtest(
        splits["validation"], cfg.exit, cfg.cost,
        cfg.model.long_threshold, cfg.model.short_threshold,
        funding_df, cfg.model.strict_funding,
    )
    val_metrics = compute_trade_metrics(val_trades, cfg.model.seed)

    # Test backtest
    test_trades = run_backtest(
        splits["test"], cfg.exit, cfg.cost,
        cfg.model.long_threshold, cfg.model.short_threshold,
        funding_df, cfg.model.strict_funding,
    )
    test_metrics = compute_trade_metrics(test_trades, cfg.model.seed)

    summary.calibration = cal_summary
    summary.validation_metrics = val_metrics
    summary.test_metrics = test_metrics

    # --- Gate evaluation (top-to-bottom, first failure wins) ---
    # Already checked: data integrity, lookahead audit, row counts, validation AUC

    # Test trade count
    if test_metrics.total_trades < cfg.model.min_test_trades:
        summary.status = "ML_ATR_V0_INSUFFICIENT_TEST_TRADES"
        summary.reason = f"test_trades_{test_metrics.total_trades}_below_{cfg.model.min_test_trades}"
        return summary

    # Side coverage
    if test_metrics.long_trades < cfg.model.min_test_long_trades or test_metrics.short_trades < cfg.model.min_test_short_trades:
        summary.status = "ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED"
        summary.reason = "insufficient_side_coverage"
        return summary

    # Economic gates
    econ_fail = False
    if test_metrics.mean_net_bps <= 0:
        econ_fail = True
    if test_metrics.median_net_bps <= 0:
        econ_fail = True
    if test_metrics.win_rate < 0.52:
        econ_fail = True
    if test_metrics.profit_factor <= 1.05:
        econ_fail = True
    if not np.isfinite(cal_summary.test_brier):
        econ_fail = True
    if not np.isfinite(test_ece):
        econ_fail = True

    if econ_fail:
        summary.status = "ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED"
        summary.reason = "economic_gates_failed"
        return summary

    # Regime shift warning
    if summary.validation_auc - summary.test_metrics.mean_net_bps > 0.05:
        summary.warnings.append("possible_regime_shift")

    # All pass
    summary.status = "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE"

    # --- Feature correlation matrix ---
    train_corr = splits["train"][feature_cols].corr()
    feature_correlations = {}
    for col in feature_cols:
        feature_correlations[col] = {row: float(train_corr.loc[col, row]) for row in feature_cols}

    # Coefficient warning
    if backend == "sklearn":
        std_coefs = coef * scaler_scale
    else:
        std_coefs = coef * scaler_scale
    if np.max(np.abs(std_coefs)) > 5.0:
        summary.warnings.append("possible_overfit_or_collinearity")

    # --- Write artifacts ---
    if not cfg.dry_run:
        # trades.csv
        write_trades_csv(test_trades, output_dir / "trades.csv", cfg.model.csv_float_format)

        # feature_coefficients.csv
        write_feature_coefficients(
            feature_cols, coef, scaler_mean, scaler_scale,
            output_dir / "feature_coefficients.csv", cfg.model.csv_float_format,
        )

        # reliability CSVs
        write_reliability_csv(val_table, output_dir / "validation_reliability.csv", cfg.model.csv_float_format)
        write_reliability_csv(test_table, output_dir / "test_reliability.csv", cfg.model.csv_float_format)

        # config.json
        write_config_json(cfg, output_dir / "config.json")

        # summary.md
        write_summary_md(summary, output_dir / "summary.md", not cfg.model.no_funding, cfg.model.min_test_trades)

        # summary.json
        summary_dict = {
            "status": summary.status,
            "reason": summary.reason,
            "study_id": summary.study_id,
            "spec_version": summary.spec_version,
            "run_id": summary.run_id,
            "config_hash": summary.config_hash,
            "train_rows": summary.train_rows,
            "validation_rows": summary.validation_rows,
            "test_rows": summary.test_rows,
            "train_auc": summary.train_auc,
            "validation_auc": summary.validation_auc,
            "validation_metrics": asdict(summary.validation_metrics) if summary.validation_metrics else None,
            "test_metrics": asdict(summary.test_metrics) if summary.test_metrics else None,
            "calibration": asdict(summary.calibration) if summary.calibration else None,
            "warnings": summary.warnings,
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary_dict, sort_keys=True, indent=2, default=str) + "\n"
        )

        # manifest.json
        git_sha = "unknown"
        try:
            import subprocess
            r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                git_sha = r.stdout.strip()
        except Exception:
            pass

        manifest = {
            "spec_version": SPEC_VERSION,
            "run_id": summary.run_id,
            "study_id": STUDY_ID,
            "git_sha": git_sha,
            "config_hash": summary.config_hash,
            "timestamp_dtype": timestamp_dtype,
            "n_bars_raw": n_bars_raw,
            "split_boundaries": {
                "train_end": cfg.split.train_end,
                "validation_start": cfg.split.validation_start,
                "validation_end": cfg.split.validation_end,
                "test_start": cfg.split.test_start,
            },
            "ohlc_sanity_rejects": ohlc_stats["ohlc_sanity_rejects"],
            "ohlc_sanity_reject_rate": ohlc_stats["ohlc_sanity_reject_rate"],
            "zero_volume_bar_count": ohlc_stats["zero_volume_bar_count"],
            "data_gaps": gaps,
            "gap_hours_by_symbol": gap_hours,
            "funding_sanity_rejects": funding_sanity_rejects,
            "funding_interval_hours": cfg.cost.funding_interval_hours,
            "max_abs_funding_rate": cfg.cost.max_abs_funding_rate,
            "feature_correlations": feature_correlations,
            "resolved_model_backend": backend,
            "numpy_fallback_solver": numpy_fallback_solver,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": sys.version,
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "sklearn_version": getattr(sys.modules.get("sklearn", None), "__version__", "unavailable"),
            "seed": cfg.model.seed,
            "fee_bps_per_side": cfg.cost.fee_bps_per_side,
            "slippage_bps_per_side": cfg.cost.slippage_bps_per_side,
            "reference_hyperliquid_taker_fee_bps": cfg.cost.reference_taker_fee_bps,
            "reference_hyperliquid_maker_fee_bps": cfg.cost.reference_maker_fee_bps,
            "safety_flags": {
                "observer_only": True,
                "no_orders": True,
                "no_auth": True,
                "no_live_execution": True,
                "no_paper_broker": True,
                "no_private_keys": True,
                "no_exchange_signing": True,
            },
        }
        write_manifest(manifest, output_dir / "manifest.json")

        # INPUTS.md
        input_files = [{"name": "bars", "path": str(bars_path), "sha256": _file_hash(bars_path), "rows": n_bars_raw}]
        if funding_path and funding_path.exists():
            input_files.append({"name": "funding", "path": str(funding_path), "sha256": _file_hash(funding_path), "rows": len(funding_df) if funding_df is not None else 0})
        write_inputs_md(input_files, output_dir / "INPUTS.md")

        # model_bundle.json
        platt_a = None
        platt_b = None
        if cal_summary.method == "platt":
            platt_a = cal_summary.a
            platt_b = cal_summary.b
        write_model_bundle(
            output_dir=output_dir,
            cfg=cfg,
            coef=coef,
            intercept=intercept,
            scaler_mean=scaler_mean,
            scaler_scale=scaler_scale,
            platt_a=platt_a,
            platt_b=platt_b,
            summary=summary,
            bars_path=bars_path,
            funding_path=funding_path,
            feature_cols=feature_cols,
            backend=backend,
            timestamp_dtype=timestamp_dtype,
            splits=splits,
        )

    return summary
