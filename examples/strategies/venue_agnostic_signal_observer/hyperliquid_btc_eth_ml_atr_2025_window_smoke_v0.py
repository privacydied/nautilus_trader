"""
Hyperliquid BTC/ETH ML+ATR 2025 Window Smoke Diagnostic v0
===========================================================

A separate, explicitly limited smoke diagnostic using only official
Hyperliquid S3 data that actually exists for 2025+.

NOT original v0. NOT evidence of robust multi-regime profitability.
Maximum claim: pipeline/signal diagnostic only.

Status namespace: ML_ATR_2025_WINDOW_SMOKE_V0_*

Forbidden statuses:
  ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE
  PAPER_SIM_V0_*
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

# Reuse original v0 functions where possible
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_btc_eth_ml_atr_v0 import (
    MlAtrConfig,
    BacktestTrade,
    CalibrationSummary,
    BacktestSummary,
    RunSummary,
    FeatureConfig,
    CostConfig,
    ExitConfig,
    ALLOWED_STATUSES as V0_ALLOWED_STATUSES,
    FORBIDDEN_STATUSES as V0_FORBIDDEN_STATUSES,
    CSV_FLOAT_FMT,
    compute_atr,
    compute_brier,
    compute_rsi_wilder,
    compute_trade_metrics,
    generate_features,
    generate_labels,
    load_bars,
    load_funding,
    run_backtest,
    validate_ohlc,
    validate_timestamp_alignment,
    detect_gaps,
    validate_funding,
    calibrate_platt,
    apply_platt,
    compute_reliability,
    _train_sklearn,
    _predict_sklearn,
    _train_numpy_fallback,
    _predict_numpy,
    _compute_auc,
    write_trades_csv,
    write_feature_coefficients,
    write_reliability_csv,
    write_config_json,
    _file_hash,
    _config_hash,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STUDY_ID = "hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0"
SPEC_VERSION = "v0"

ALLOWED_STATUSES = {
    "ML_ATR_2025_WINDOW_SMOKE_V0_BACKTEST_COMPLETE_PIPELINE_VALIDATED",
    "ML_ATR_2025_WINDOW_SMOKE_V0_SIGNAL_PRESENT_NOT_PROMOTABLE",
    "ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA",
    "ML_ATR_2025_WINDOW_SMOKE_V0_CALIBRATION_FAILED",
    "ML_ATR_2025_WINDOW_SMOKE_V0_INSUFFICIENT_TEST_TRADES",
    "ML_ATR_2025_WINDOW_SMOKE_V0_ECONOMIC_GATES_FAILED",
    "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT",
    "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_LOOKAHEAD_AUDIT_FAILED",
    "ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_COST_OR_SIZE_CAP",
    "ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_SCHEMA_UNRECOGNIZED",
    "ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_PARSE_FAILED",
}

FORBIDDEN_STATUSES = {
    "ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE",
    "PAPER_SIM_V0_*",
    "TRADE_READY",
    "EXECUTION_READY",
    "LIVE_READY",
    "CANDIDATE_FOR_LIVE",
    "PROFITABLE",
}

# ---------------------------------------------------------------------------
# Frozen Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SplitConfig2025:
    train_start: str = "2025-08-01T00:00:00Z"
    train_end: str = "2025-12-31T23:59:59Z"
    validation_start: str = "2026-01-01T00:00:00Z"
    validation_end: str = "2026-02-28T23:59:59Z"
    test_start: str = "2026-03-01T00:00:00Z"
    min_train_rows: int = 3000
    min_validation_rows: int = 1000
    min_test_rows: int = 1000


@dataclass(frozen=True)
class ModelConfig2025:
    long_threshold: float = 0.55
    short_threshold: float = 0.40
    model_backend: str = "auto"
    calibrator: str = "platt"
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
class MLATR2025WindowConfig:
    split: SplitConfig2025 = field(default_factory=SplitConfig2025)
    model: ModelConfig2025 = field(default_factory=ModelConfig2025)
    symbols: Tuple[str, ...] = ("BTC", "ETH")
    dry_run: bool = False


@dataclass(frozen=True)
class MLATR2025WindowSplit:
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: Optional[str] = None


@dataclass
class MLATR2025WindowSummary:
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
    train_auc: float = 0.0
    validation_auc: float = 0.0
    validation_metrics: Optional[BacktestSummary] = None
    test_metrics: Optional[BacktestSummary] = None
    calibration: Optional[CalibrationSummary] = None
    warnings: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)
    source_caveat: str = (
        "Single recent regime (2025-08 to 2026-03+). "
        "Insufficient for robust multi-regime validation. "
        "Not comparable to original late-2023 v0."
    )
    promotion_caveat: str = (
        "Even if metrics pass, this does NOT unlock paper, live, shadow, "
        "bot, or systemd execution. Separate precommitment required."
    )


# ---------------------------------------------------------------------------
# Split Logic
# ---------------------------------------------------------------------------
def derive_2025_window_splits(cfg: MLATR2025WindowConfig) -> MLATR2025WindowSplit:
    """Derive frozen 2025-window split boundaries."""
    return MLATR2025WindowSplit(
        train_start=cfg.split.train_start,
        train_end=cfg.split.train_end,
        validation_start=cfg.split.validation_start,
        validation_end=cfg.split.validation_end,
        test_start=cfg.split.test_start,
    )


def validate_2025_window_inputs(
    bars: pd.DataFrame,
    funding_df: Optional[pd.DataFrame],
    cfg: MLATR2025WindowConfig,
) -> Tuple[Dict[str, pd.DataFrame], MLATR2025WindowSplit, List[str]]:
    """Validate inputs and return splits + warnings."""
    warnings_list = []
    splits_cfg = derive_2025_window_splits(cfg)

    train_start = pd.Timestamp(cfg.split.train_start)
    train_end = pd.Timestamp(cfg.split.train_end)
    val_start = pd.Timestamp(cfg.split.validation_start)
    val_end = pd.Timestamp(cfg.split.validation_end)
    test_start = pd.Timestamp(cfg.split.test_start)

    # Check both symbols present
    sym_set = set(bars["symbol"].unique())
    for sym in cfg.symbols:
        if sym not in sym_set:
            raise ValueError(f"Missing symbol {sym} in bar data")

    # Check funding coverage
    if funding_df is not None and len(funding_df) > 0:
        fund_ts = funding_df["timestamp"]
        for sym in cfg.symbols:
            fsub = funding_df[funding_df["symbol"] == sym]
            if len(fsub) == 0:
                raise ValueError(f"Missing funding data for {sym}")
            fund_min = fsub["timestamp"].min()
            fund_max = fsub["timestamp"].max()
            if fund_min > train_start:
                warnings_list.append(
                    f"funding for {sym} starts at {fund_min} "
                    f"after train start {train_start}"
                )
            if fund_max < test_start:
                warnings_list.append(
                    f"funding for {sym} ends at {fund_max} "
                    f"before test start {test_start}"
                )
    else:
        warnings_list.append("No funding data provided")

    return splits_cfg, warnings_list


def assign_2025_window_splits(
    df: pd.DataFrame,
    cfg: MLATR2025WindowConfig,
) -> Dict[str, pd.DataFrame]:
    """Assign rows to train/validation/test with frozen 2025-window dates."""
    train_start = pd.Timestamp(cfg.split.train_start)
    train_end = pd.Timestamp(cfg.split.train_end)
    val_start = pd.Timestamp(cfg.split.validation_start)
    val_end = pd.Timestamp(cfg.split.validation_end)
    test_start = pd.Timestamp(cfg.split.test_start)

    feature_cols = list(cfg.model.feature_names if hasattr(cfg.model, 'feature_names')
                        else ["ret_1h", "ret_4h", "ret_24h", "realized_vol_24h",
                              "atr_norm_14h", "funding_current", "funding_mean_24h", "rsi_14h"])

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


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------
def run_2025_window_smoke_diagnostic(
    cfg: MLATR2025WindowConfig,
    bars_path: Path,
    funding_path: Optional[Path],
    output_root: Path,
    symbols: Tuple[str, ...] = ("BTC", "ETH"),
) -> MLATR2025WindowSummary:
    """
    Execute the full 2025-window smoke diagnostic pipeline.
    Returns MLATR2025WindowSummary.
    """
    run_id = f"smoke_{cfg.model.seed}"
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = MLATR2025WindowSummary(
        status="",
        run_id=run_id,
        config_hash=_config_hash(cfg),
    )

    # --- Load data ---
    bars = load_bars(bars_path, symbols)
    timestamp_dtype = bars.attrs.get("timestamp_dtype", "unknown")
    n_bars_raw = len(bars)

    # Check both symbols present (before any filtering)
    sym_set = set(bars["symbol"].unique())
    for sym in symbols:
        if sym not in sym_set:
            summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"
            summary.reason = f"Missing symbol {sym} in bar data"
            return summary

    bars = bars[bars["symbol"].isin(symbols)].copy()

    # OHLC sanity
    bars, ohlc_stats = validate_ohlc(bars)
    if ohlc_stats["ohlc_sanity_reject_rate"] > 0.001:
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"
        summary.reason = "ohlc_reject_rate_exceeds_tolerance"
        return summary

    # Timestamp alignment
    bars, align_stats = validate_timestamp_alignment(bars)
    if align_stats["timestamp_misaligned_rate"] > 0.001:
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"
        summary.reason = "timestamp_misalignment_exceeds_tolerance"
        return summary

    # Gap detection
    gaps, gap_hours = detect_gaps(bars, cfg.model.max_gap_hours)
    for sym, gh in gap_hours.items():
        if gh > cfg.model.max_gap_hours:
            summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"
            summary.reason = f"gap_hours_exceeds_max for {sym}"
            return summary

    # Funding
    funding_df = None
    funding_sanity_rejects = 0
    if not cfg.model.no_funding and funding_path is not None:
        funding_df = load_funding(funding_path)
        funding_df, f_stats = validate_funding(funding_df, CostConfig(), cfg.model.no_funding)
        funding_sanity_rejects = f_stats.get("funding_sanity_rejects", 0)

    # --- Features per symbol ---
    feature_names = list(("ret_1h", "ret_4h", "ret_24h", "realized_vol_24h",
                          "atr_norm_14h", "funding_current", "funding_mean_24h", "rsi_14h"))
    fc = FeatureConfig()
    cc = CostConfig()
    all_dfs = []
    for sym in sorted(bars["symbol"].unique()):
        sub = bars[bars["symbol"] == sym].copy()
        fdf = generate_features(sub, funding_df, fc, cc, cfg.model.no_funding)
        fdf = generate_labels(fdf, 24)
        all_dfs.append(fdf)

    bars_full = pd.concat(all_dfs, ignore_index=True)

    # --- Splits ---
    try:
        splits = assign_2025_window_splits(bars_full, cfg)
    except AssertionError as e:
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_LOOKAHEAD_AUDIT_FAILED"
        summary.reason = str(e)
        return summary

    # Row count checks
    for split_name, min_rows in [
        ("train", cfg.split.min_train_rows),
        ("validation", cfg.split.min_validation_rows),
        ("test", cfg.split.min_test_rows),
    ]:
        if len(splits[split_name]) < min_rows:
            summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA"
            summary.reason = f"{split_name}_has_{len(splits[split_name])}_rows_below_{min_rows}"
            return summary

    # Label balance
    for split_name in ["train", "validation", "test"]:
        s = splits[split_name]
        pos_pct = float(s["label_up"].mean())
        if pos_pct < 0.30 or pos_pct > 0.70:
            summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"
            summary.reason = f"degenerate_label_balance_in_{split_name}"
            return summary

    summary.train_rows = len(splits["train"])
    summary.validation_rows = len(splits["validation"])
    summary.test_rows = len(splits["test"])
    summary.train_positive_pct = float(splits["train"]["label_up"].mean() * 100)
    summary.validation_positive_pct = float(splits["validation"]["label_up"].mean() * 100)
    summary.test_positive_pct = float(splits["test"]["label_up"].mean() * 100)

    # --- Model ---
    feature_cols = list(feature_names)
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
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT"
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
    if backend == "sklearn":
        train_probs = _predict_sklearn(coef, intercept, scaler_obj, X_train)
    else:
        train_probs = _predict_numpy(coef, intercept, scaler_obj.transform(X_train))
    summary.train_auc = _compute_auc(train_probs, y_train)
    summary.validation_auc = _compute_auc(val_probs, y_val)

    # Validation AUC gate
    if summary.validation_auc < 0.51:
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_CALIBRATION_FAILED"
        summary.reason = "validation_auc_below_0.51"
        return summary

    # --- Calibration ---
    if cfg.model.calibrator == "isotonic":
        from sklearn.isotonic import IsotonicRegression
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(val_probs, y_val)
        cal_val_probs = iso.predict(val_probs)
        cal_test_probs = iso.predict(test_probs_raw)
        cal_summary = CalibrationSummary(method="isotonic")
    else:
        if len(np.unique(y_val)) < 2:
            summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_CALIBRATION_FAILED"
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
    cost_cfg = CostConfig()
    exit_cfg = ExitConfig()

    val_trades = run_backtest(
        splits["validation"], exit_cfg, cost_cfg,
        cfg.model.long_threshold, cfg.model.short_threshold,
        funding_df, cfg.model.strict_funding,
    )
    val_metrics = compute_trade_metrics(val_trades, cfg.model.seed)

    test_trades = run_backtest(
        splits["test"], exit_cfg, cost_cfg,
        cfg.model.long_threshold, cfg.model.short_threshold,
        funding_df, cfg.model.strict_funding,
    )
    test_metrics = compute_trade_metrics(test_trades, cfg.model.seed)

    summary.calibration = cal_summary
    summary.validation_metrics = val_metrics
    summary.test_metrics = test_metrics

    # --- Gate evaluation ---
    if test_metrics.total_trades < cfg.model.min_test_trades:
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_INSUFFICIENT_TEST_TRADES"
        summary.reason = f"test_trades_{test_metrics.total_trades}_below_{cfg.model.min_test_trades}"
        return summary

    if test_metrics.long_trades < cfg.model.min_test_long_trades or test_metrics.short_trades < cfg.model.min_test_short_trades:
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ECONOMIC_GATES_FAILED"
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
        summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_ECONOMIC_GATES_FAILED"
        summary.reason = "economic_gates_failed"
        return summary

    # All pass — but NOT shadow-logging eligible
    summary.status = "ML_ATR_2025_WINDOW_SMOKE_V0_BACKTEST_COMPLETE_PIPELINE_VALIDATED"

    summary.warnings.extend(val_warns + test_warns)
    summary.warnings.append(summary.source_caveat)
    summary.warnings.append(summary.promotion_caveat)

    # --- Write artifacts ---
    if not cfg.dry_run:
        write_trades_csv(test_trades, output_dir / "trades.csv", cfg.model.csv_float_format)
        write_feature_coefficients(
            feature_cols, coef, scaler_mean, scaler_scale,
            output_dir / "feature_coefficients.csv", cfg.model.csv_float_format,
        )
        write_reliability_csv(val_table, output_dir / "validation_reliability.csv", cfg.model.csv_float_format)
        write_reliability_csv(test_table, output_dir / "test_reliability.csv", cfg.model.csv_float_format)
        write_config_json(cfg, output_dir / "config.json")
        write_summary_md(summary, output_dir / "summary.md")
        write_summary_json(summary, output_dir / "summary.json")
        write_manifest(summary, output_dir / "manifest.json", bars_path, funding_path,
                       splits, ohlc_stats, gap_hours, funding_sanity_rejects,
                       timestamp_dtype, backend, numpy_fallback_solver,
                       feature_cols, scaler_mean, scaler_scale, a, b if cal_summary.method == "platt" else None)
        write_inputs_md([{"name": "bars", "path": str(bars_path), "sha256": _file_hash(bars_path),
                          "rows": n_bars_raw}], output_dir / "INPUTS.md")
        write_model_bundle_smoke(output_dir, cfg, coef, intercept, scaler_mean, scaler_scale,
                                 a if cal_summary.method == "platt" else None,
                                 b if cal_summary.method == "platt" else None,
                                 summary, bars_path, funding_path, feature_cols, backend,
                                 timestamp_dtype, splits)

    return summary


# ---------------------------------------------------------------------------
# Artifact Writing
# ---------------------------------------------------------------------------
def write_summary_md(summary: MLATR2025WindowSummary, path: Path):
    """Write summary.md with DO NOT USE header."""
    lines = [
        "# DO NOT USE FOR LIVE TRADING",
        "",
        "## This is NOT original v0",
        "",
        "This is a **separate, explicitly limited 2025-window smoke diagnostic**.",
        "It is NOT original v0. It does NOT unlock paper, live, shadow, bot, or systemd.",
        "",
        "## Status",
        f"Status: {summary.status}",
    ]
    if summary.reason:
        lines.append(f"Reason: {summary.reason}")
    lines.extend([
        "",
        f"Study: {summary.study_id}",
        f"Spec version: {summary.spec_version}",
        f"Run ID: {summary.run_id}",
        "",
        "## Regime Caveat",
        "",
        "- **Single recent regime** (2025-08 through 2026-03+).",
        "- **Insufficient for robust multi-regime validation**.",
        "- **Not comparable** to the original late-2023 v0.",
        "",
        "## Split Boundaries",
        f"- Train: {summary.manifest.get('split_boundaries', {}).get('train_start', '')} to {summary.manifest.get('split_boundaries', {}).get('train_end', '')}",
        f"- Validation: {summary.manifest.get('split_boundaries', {}).get('validation_start', '')} to {summary.manifest.get('split_boundaries', {}).get('validation_end', '')}",
        f"- Test: {summary.manifest.get('split_boundaries', {}).get('test_start', '')} to {summary.manifest.get('split_boundaries', {}).get('test_end', 'latest')}",
        "",
        "## Row Counts",
        f"- Train rows: {summary.train_rows}",
        f"- Validation rows: {summary.validation_rows}",
        f"- Test rows: {summary.test_rows}",
        "",
    ])

    if summary.test_metrics:
        m = summary.test_metrics
        lines.extend([
            "## Test Metrics",
            f"- Total trades: {m.total_trades}",
            f"- Long trades: {m.long_trades}",
            f"- Short trades: {m.short_trades}",
            f"- Mean net bps: {m.mean_net_bps:.4f}",
            f"- Median net bps: {m.median_net_bps:.4f}",
            f"- Win rate: {m.win_rate:.4f}",
            f"- Profit factor: {m.profit_factor:.4f}",
            "",
        ])

    if summary.calibration:
        c = summary.calibration
        lines.extend([
            "## Calibration",
            f"- Method: {c.method}",
            f"- Validation Brier: {c.validation_brier:.6f}",
            f"- Validation ECE: {c.validation_ece:.6f}",
            f"- Validation MCE: {c.validation_mce:.6f}",
            f"- Test Brier: {c.test_brier:.6f}",
            f"- Test ECE: {c.test_ece:.6f}",
            f"- Test MCE: {c.test_mce:.6f}",
            "",
        ])

    lines.extend([
        "## Authorization",
        "",
        "This diagnostic output does NOT authorize:",
        "- Shadow logging",
        "- Exchange paper",
        "- Bot routing",
        "- Live trading",
        "- Order routing",
        "- Systemd",
        "",
        "A separate precommitment is required for any future paper/shadow run.",
        "",
    ])

    if summary.warnings:
        lines.extend(["## Warnings"] + [f"- {w}" for w in summary.warnings])
        lines.append("")

    path.write_text("\n".join(lines) + "\n")


def write_summary_json(summary: MLATR2025WindowSummary, path: Path):
    """Write summary.json."""
    d = {
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
        "train_positive_pct": summary.train_positive_pct,
        "validation_positive_pct": summary.validation_positive_pct,
        "test_positive_pct": summary.test_positive_pct,
        "validation_metrics": asdict(summary.validation_metrics) if summary.validation_metrics else None,
        "test_metrics": asdict(summary.test_metrics) if summary.test_metrics else None,
        "calibration": asdict(summary.calibration) if summary.calibration else None,
        "warnings": summary.warnings,
        "source_caveat": summary.source_caveat,
        "promotion_caveat": summary.promotion_caveat,
    }
    path.write_text(json.dumps(d, sort_keys=True, indent=2, default=str) + "\n")


def write_manifest(
    summary: MLATR2025WindowSummary,
    path: Path,
    bars_path: Path,
    funding_path: Optional[Path],
    splits: Dict[str, pd.DataFrame],
    ohlc_stats: Dict[str, Any],
    gap_hours: Dict[str, int],
    funding_sanity_rejects: int,
    timestamp_dtype: str,
    backend: str,
    numpy_fallback_solver: Optional[str],
    feature_cols: List[str],
    scaler_mean: np.ndarray,
    scaler_scale: np.ndarray,
    platt_a: Optional[float],
    platt_b: Optional[float],
):
    """Write manifest.json."""
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
        "n_bars_raw": ohlc_stats.get("ohlc_sanity_rejects", 0),
        "split_boundaries": {
            "train_start": splits.get("train_start", ""),
            "train_end": splits.get("train_end", ""),
            "validation_start": splits.get("validation_start", ""),
            "validation_end": splits.get("validation_end", ""),
            "test_start": splits.get("test_start", ""),
            "test_end": splits.get("test_end"),
        },
        "ohlc_sanity_rejects": ohlc_stats.get("ohlc_sanity_rejects", 0),
        "ohlc_sanity_reject_rate": ohlc_stats.get("ohlc_sanity_reject_rate", 0.0),
        "data_gaps": [],
        "gap_hours_by_symbol": gap_hours,
        "funding_sanity_rejects": funding_sanity_rejects,
        "resolved_model_backend": backend,
        "numpy_fallback_solver": numpy_fallback_solver,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "sklearn_version": getattr(sys.modules.get("sklearn", None), "__version__", "unavailable"),
        "seed": 42,
        "thresholds": {
            "long_threshold": 0.55,
            "short_threshold": 0.40,
        },
        "source_coverage": {
            "source": "node_fills_by_block/hourly",
            "caveat": "Single recent regime (2025-08 to 2026-03+). Insufficient for robust multi-regime validation.",
        },
        "split_caveat": "Single recent regime. Not comparable to original late-2023 v0.",
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
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2, default=str) + "\n")


def write_model_bundle_smoke(
    output_dir: Path,
    cfg: MLATR2025WindowConfig,
    coef: np.ndarray,
    intercept: float,
    scaler_mean: np.ndarray,
    scaler_scale: np.ndarray,
    platt_a: Optional[float],
    platt_b: Optional[float],
    summary: MLATR2025WindowSummary,
    bars_path: Path,
    funding_path: Optional[Path],
    feature_cols: List[str],
    backend: str,
    timestamp_dtype: str,
    splits: Dict[str, pd.DataFrame],
) -> Path:
    """Export model bundle with paper/live/shadow = false."""
    bundle = {
        "spec_version": SPEC_VERSION,
        "study_id": STUDY_ID,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run_id": summary.run_id,
        "source_summary_status": summary.status,
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
        "train_window": {"start": splits.get("train_start"), "end": splits.get("train_end")},
        "validation_window": {"start": splits.get("validation_start"), "end": splits.get("validation_end")},
        "test_window": {"start": splits.get("test_start"), "end": splits.get("test_end")},
        "paper_eligible": False,
        "live_eligible": False,
        "shadow_logging_eligible": False,
        "source_caveat": summary.source_caveat,
        "promotion_caveat": summary.promotion_caveat,
        "eligibility_status_from_source_summary": summary.status,
        "safety": {
            "observer_only": True,
            "no_orders": True,
            "no_auth": True,
            "no_live_execution": True,
        },
    }
    bundle_path = output_dir / "model_bundle.json"
    bundle_path.write_text(json.dumps(bundle, sort_keys=True, indent=2, default=str) + "\n")
    return bundle_path


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
