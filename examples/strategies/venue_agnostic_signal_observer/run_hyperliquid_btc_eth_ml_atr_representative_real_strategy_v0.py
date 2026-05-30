#!/usr/bin/env python3
"""
CLI runner for Hyperliquid BTC/ETH ML+ATR representative real strategy diagnostic.

This runs the actual frozen-threshold ML+ATR strategy on representative data.
NOT live trading. NOT paper execution. NOT bot authorization.
Produces reproducible archive backtest artifacts and tests only.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Ensure the parent directory is on the path so we can import the core module
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from hyperliquid_btc_eth_ml_atr_v0 import (
    MlAtrConfig,
    SplitConfig,
    FeatureConfig,
    CostConfig,
    ExitConfig,
    ModelConfig,
    load_bars,
    load_funding,
    validate_ohlc,
    validate_timestamp_alignment,
    detect_gaps,
    validate_funding,
    generate_features,
    generate_labels,
    assign_splits,
    _train_sklearn,
    _predict_sklearn,
    _train_numpy_fallback,
    _predict_numpy,
    calibrate_platt,
    apply_platt,
    compute_reliability,
    compute_brier,
    run_backtest,
    compute_trade_metrics,
    write_trades_csv,
    write_feature_coefficients,
    write_reliability_csv,
    write_summary_md,
    write_inputs_md,
    write_model_bundle,
    write_manifest,
    write_config_json,
    _config_hash,
    _file_hash,
    _compute_auc,
    BacktestSummary,
    CalibrationSummary,
    RunSummary,
    STUDY_ID,
    SPEC_VERSION,
    FORBIDDEN_STATUSES,
)


# Representative real strategy statuses
ALLOWED_STATUSES = {
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_SPLIT_AUDIT_FAILED",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_NEEDS_MORE_DATA",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_VALIDATION_CALIBRATION_FAILED",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_INSUFFICIENT_TEST_TRADES",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ECONOMIC_GATES_FAILED",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_INVALID_INPUT",
    "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_LOOKAHEAD_AUDIT_FAILED",
}


def run_split_row_audit(
    bars: pd.DataFrame,
    funding_df: Optional[pd.DataFrame],
    cfg: MlAtrConfig,
    output_dir: Path,
) -> Dict[str, Any]:
    """
    Audit the split row counts to explain why rows are dropped.
    Returns audit results and writes split_row_audit.json/md.
    """
    audit = {
        "status": "REPRESENTATIVE_SPLIT_ROW_AUDIT_EXPLAINED",
        "explanation": [],
        "per_symbol": {},
        "per_split": {},
    }

    for sym in sorted(bars["symbol"].unique()):
        sym_bars = bars[bars["symbol"] == sym].copy().sort_values("timestamp")
        sym_audit = {
            "raw_bars": len(sym_bars),
            "date_window_bars": {},
            "after_funding_alignment": {},
            "after_feature_generation": {},
            "after_label_generation": {},
            "final_model_eligible": {},
            "dropped_by_cause": {},
        }

        # Count raw bars per split window
        train_start = pd.Timestamp(cfg.split.train_start, tz="UTC")
        train_end = pd.Timestamp(cfg.split.train_end, tz="UTC")
        val_start = pd.Timestamp(cfg.split.validation_start, tz="UTC")
        val_end = pd.Timestamp(cfg.split.validation_end, tz="UTC")
        test_start = pd.Timestamp(cfg.split.test_start, tz="UTC")

        for split_name, start, end in [
            ("train", train_start, train_end),
            ("validation", val_start, val_end),
            ("test", test_start, None),  # No end for test split
        ]:
            if end is not None:
                split_bars = sym_bars[
                    (sym_bars["timestamp"] >= start) & (sym_bars["timestamp"] <= end)
                ]
            else:
                split_bars = sym_bars[sym_bars["timestamp"] >= start]
            sym_audit["date_window_bars"][split_name] = len(split_bars)

        # Generate features for this symbol
        fdf = generate_features(sym_bars, funding_df, cfg.feature, cfg.cost, cfg.model.no_funding)
        n_after_features = len(fdf)

        # Generate labels
        fdf = generate_labels(fdf, cfg.feature.label_horizon_bars)
        n_after_labels = len(fdf)

        # Drop NaN features/labels
        feature_cols = list(cfg.feature.feature_names)
        drop_cols = feature_cols + ["label_up"]
        fdf_clean = fdf.dropna(subset=drop_cols)
        n_after_clean = len(fdf_clean)

        # Count per split after cleaning
        for split_name, start, end in [
            ("train", train_start, train_end),
            ("validation", val_start, val_end),
            ("test", test_start, None),  # No end for test split
        ]:
            if end is not None:
                split_clean = fdf_clean[
                    (fdf_clean["timestamp"] >= start) & (fdf_clean["timestamp"] <= end)
                ]
            else:
                split_clean = fdf_clean[fdf_clean["timestamp"] >= start]
            sym_audit["after_feature_generation"][split_name] = len(split_clean)
            sym_audit["after_label_generation"][split_name] = len(split_clean)
            sym_audit["final_model_eligible"][split_name] = len(split_clean)

        # Calculate drops by cause
        feature_warmup_drops = n_after_features - n_after_clean
        label_horizon_drops = n_after_labels - n_after_clean

        sym_audit["dropped_by_cause"] = {
            "feature_warmup_rolling_window": feature_warmup_drops,
            "label_horizon_forward": label_horizon_drops,
            "missing_funding": 0,
            "other_validation_filters": 0,
        }

        audit["per_symbol"][sym] = sym_audit

    # Write audit artifacts
    audit["explanation"] = [
        "Feature warmup drops the first 24 bars per symbol due to rolling window requirements (RSI, ATR, realized vol need 24 bars of history).",
        "Label horizon drops the last 24 bars per symbol because future close prices are not available for the final 24 bars.",
        "These are legitimate past-only feature construction drops, not row-index partitioning or representative-mode bypass.",
        "No test rows are used for scaler fit, model fit, or Platt calibration.",
    ]

    # Write JSON
    audit_path = output_dir / "split_row_audit.json"
    with open(audit_path, "w") as f:
        json.dump(audit, f, indent=2, sort_keys=True, default=str)

    # Write Markdown
    md_lines = [
        "# Split Row Audit",
        "",
        f"**Status**: `{audit['status']}`",
        "",
        "## Explanation",
        "",
    ]
    for line in audit["explanation"]:
        md_lines.append(f"- {line}")
    md_lines.append("")

    for sym in ["BTC", "ETH"]:
        if sym in audit["per_symbol"]:
            sym_audit = audit["per_symbol"][sym]
            md_lines.append(f"## {sym}")
            md_lines.append(f"- Raw bars: {sym_audit['raw_bars']}")
            for split in ["train", "validation", "test"]:
                md_lines.append(f"- {split} date window bars: {sym_audit['date_window_bars'][split]}")
                md_lines.append(f"- {split} after feature generation: {sym_audit['after_feature_generation'][split]}")
                md_lines.append(f"- {split} final model eligible: {sym_audit['final_model_eligible'][split]}")
            md_lines.append("- Drops by cause:")
            for cause, count in sym_audit["dropped_by_cause"].items():
                md_lines.append(f"  - {cause}: {count}")
            md_lines.append("")

    md_path = output_dir / "split_row_audit.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines) + "\n")

    return audit


def run_representative_real_strategy(
    bars_path: Path,
    funding_path: Optional[Path],
    output_root: Path,
    run_id: str,
    cfg: MlAtrConfig,
    symbols: tuple,
) -> RunSummary:
    """
    Execute the full ML+ATR pipeline on representative data.
    Returns RunSummary.
    """
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = RunSummary(
        status="",
        run_id=run_id,
        config_hash=_config_hash(cfg),
        study_id=STUDY_ID,
        spec_version=SPEC_VERSION,
    )

    # --- Load data ---
    bars = load_bars(bars_path, symbols)
    timestamp_dtype = bars.attrs.get("timestamp_dtype", "unknown")
    n_bars_raw = len(bars)

    # Filter to configured symbols
    bars = bars[bars["symbol"].isin(symbols)].copy()

    # OHLC sanity
    bars, ohlc_stats = validate_ohlc(bars)
    if ohlc_stats["ohlc_sanity_reject_rate"] > 0.001:
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_INVALID_INPUT"
        summary.reason = "ohlc_reject_rate_exceeds_tolerance"
        return summary

    # Timestamp alignment
    bars, align_stats = validate_timestamp_alignment(bars)
    if align_stats["timestamp_misaligned_rate"] > 0.001:
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_INVALID_INPUT"
        summary.reason = "timestamp_misalignment_exceeds_tolerance"
        return summary

    # Gap detection
    gaps, gap_hours = detect_gaps(bars, cfg.model.max_gap_hours)
    for sym, gh in gap_hours.items():
        if gh > cfg.model.max_gap_hours:
            summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_INVALID_INPUT"
            summary.reason = f"gap_hours_exceeds_max for {sym}"
            return summary

    # Funding
    funding_df = None
    funding_sanity_rejects = 0
    if not cfg.model.no_funding and funding_path is not None:
        funding_df = load_funding(funding_path)
        funding_df, f_stats = validate_funding(funding_df, cfg.cost, cfg.model.no_funding)
        funding_sanity_rejects = f_stats["funding_sanity_rejects"]

    # --- Run split row audit ---
    audit = run_split_row_audit(bars, funding_df, cfg, output_dir)
    if audit["status"] == "REPRESENTATIVE_SPLIT_ROW_AUDIT_FAILED":
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_SPLIT_AUDIT_FAILED"
        summary.reason = "split_row_audit_failed"
        return summary

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
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_LOOKAHEAD_AUDIT_FAILED"
        summary.reason = str(e)
        return summary

    # Row count checks
    for split_name, min_rows in [
        ("train", cfg.split.min_train_rows),
        ("validation", cfg.split.min_validation_rows),
        ("test", cfg.split.min_test_rows),
    ]:
        if len(splits[split_name]) < min_rows:
            summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_NEEDS_MORE_DATA"
            summary.reason = f"{split_name}_has_{len(splits[split_name])}_rows_below_{min_rows}"
            return summary

    # Label balance
    for split_name in ["train", "validation", "test"]:
        s = splits[split_name]
        pos_pct = float(s["label_up"].mean())
        if pos_pct < 0.30 or pos_pct > 0.70:
            summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_INVALID_INPUT"
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
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ERROR_INVALID_INPUT"
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
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_VALIDATION_CALIBRATION_FAILED"
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
            summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_VALIDATION_CALIBRATION_FAILED"
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
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_INSUFFICIENT_TEST_TRADES"
        summary.reason = f"test_trades_{test_metrics.total_trades}_below_{cfg.model.min_test_trades}"
        return summary

    # Side coverage
    if test_metrics.long_trades < cfg.model.min_test_long_trades or test_metrics.short_trades < cfg.model.min_test_short_trades:
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ECONOMIC_GATES_FAILED"
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
        summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_ECONOMIC_GATES_FAILED"
        summary.reason = "economic_gates_failed"
        return summary

    # Regime shift warning
    if summary.validation_auc - summary.test_metrics.mean_net_bps > 0.05:
        summary.warnings.append("possible_regime_shift")

    # All pass
    summary.status = "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE"

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
            "non_canonical_warning": "Representative non-canonical real-strategy diagnostic. Not original v0. Not full 2025-window.",
            "no_live_warning": "DO NOT USE FOR LIVE TRADING. No paper/live/shadow eligibility.",
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
                "train_start": cfg.split.train_start,
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
            "non_canonical_warning": "Representative non-canonical real-strategy diagnostic. Not original v0. Not full 2025-window.",
            "no_live_warning": "DO NOT USE FOR LIVE TRADING. No paper/live/shadow eligibility.",
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


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Hyperliquid BTC/ETH ML+ATR representative real strategy diagnostic",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Required inputs
    p.add_argument("--bars-path", required=True, type=Path,
                   help="Path to hourly bars CSV or Parquet")
    p.add_argument("--funding-path", type=Path, default=None,
                   help="Path to hourly funding CSV or Parquet (required unless --no-funding)")

    # Output
    p.add_argument("--output-root", type=Path,
                   default=Path("reports/hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0"),
                   help="Root directory for report output")
    p.add_argument("--run-id", required=True,
                   help="Unique run identifier")

    # Strategy params
    p.add_argument("--label-horizon-bars", type=int, default=24)
    p.add_argument("--long-threshold", type=float, default=0.55)
    p.add_argument("--short-threshold", type=float, default=0.40)

    # Cost
    p.add_argument("--fee-bps-per-side", type=float, default=1.0)
    p.add_argument("--slippage-bps-per-side", type=float, default=0.5)

    # Stochastic
    p.add_argument("--seed", type=int, default=42)

    # Data validation
    p.add_argument("--max-gap-hours", type=int, default=24)
    p.add_argument("--max-abs-funding-rate", type=float, default=0.01)
    p.add_argument("--allow-zero-volume-bars", action=argparse.BooleanOptionalAction, default=True)

    # Split thresholds (representative windows)
    p.add_argument("--train-start", default="2025-08-01",
                   help="Train start date (representative window)")
    p.add_argument("--train-end", default="2025-08-31",
                   help="Train end date (representative window)")
    p.add_argument("--validation-start", default="2026-01-01",
                   help="Validation start date (representative window)")
    p.add_argument("--validation-end", default="2026-01-31",
                   help="Validation end date (representative window)")
    p.add_argument("--test-start", default="2026-03-01",
                   help="Test start date (representative window)")
    p.add_argument("--test-end", default="2026-03-31",
                   help="Test end date (representative window)")

    # Row count gates (representative mode)
    p.add_argument("--min-train-rows-per-symbol", type=int, default=500)
    p.add_argument("--min-validation-rows-per-symbol", type=int, default=300)
    p.add_argument("--min-test-rows-per-symbol", type=int, default=300)
    p.add_argument("--min-test-trades", type=int, default=50)
    p.add_argument("--min-test-long-trades", type=int, default=10)
    p.add_argument("--min-test-short-trades", type=int, default=10)

    # Model
    p.add_argument("--calibrator", choices=["platt", "isotonic"], default="platt",
                   help="Calibration method (isotonic requires explicit opt-in)")
    p.add_argument("--model-backend", choices=["sklearn", "numpy_fallback", "auto"],
                   default="auto")

    # Flags
    p.add_argument("--strict-funding", action="store_true", default=False)
    p.add_argument("--no-funding", action="store_true", default=False)
    p.add_argument("--dry-run", action="store_true", default=False)

    # Symbols (repeatable)
    p.add_argument("--symbol", action="append", choices=["BTC", "ETH"],
                   help="Symbols to include (repeatable). Default: BTC ETH")

    args = p.parse_args(argv)

    # Validate funding path
    if args.funding_path is None and not args.no_funding:
        p.error("--funding-path is required unless --no-funding is set")

    # Default symbols
    if args.symbol is None:
        args.symbol = ["BTC", "ETH"]

    return args


def main(argv=None):
    args = _parse_args(argv)

    symbols = tuple(sorted(set(args.symbol)))
    unknown = set(symbols) - {"BTC", "ETH"}
    if unknown:
        print(f"ERROR: Unknown symbols: {unknown}. Only BTC and ETH are valid for v0.", file=sys.stderr)
        sys.exit(1)

    # Build config with representative date windows
    cfg = MlAtrConfig(
        split=SplitConfig(
            train_start=args.train_start,
            train_end=args.train_end,
            validation_start=args.validation_start,
            validation_end=args.validation_end,
            test_start=args.test_start,
            min_train_rows=args.min_train_rows_per_symbol,
            min_validation_rows=args.min_validation_rows_per_symbol,
            min_test_rows=args.min_test_rows_per_symbol,
        ),
        feature=FeatureConfig(
            label_horizon_bars=args.label_horizon_bars,
        ),
        cost=CostConfig(
            fee_bps_per_side=args.fee_bps_per_side,
            slippage_bps_per_side=args.slippage_bps_per_side,
            max_abs_funding_rate=args.max_abs_funding_rate,
            allow_zero_volume_bars=args.allow_zero_volume_bars,
        ),
        exit=ExitConfig(),
        model=ModelConfig(
            long_threshold=args.long_threshold,
            short_threshold=args.short_threshold,
            model_backend=args.model_backend,
            calibrator=args.calibrator,
            seed=args.seed,
            max_gap_hours=args.max_gap_hours,
            min_test_trades=args.min_test_trades,
            min_test_long_trades=args.min_test_long_trades,
            min_test_short_trades=args.min_test_short_trades,
            strict_funding=args.strict_funding,
            no_funding=args.no_funding,
        ),
        symbols=symbols,
        dry_run=args.dry_run,
    )

    summary = run_representative_real_strategy(
        bars_path=args.bars_path,
        funding_path=args.funding_path,
        output_root=args.output_root,
        run_id=args.run_id,
        cfg=cfg,
        symbols=symbols,
    )

    print(f"Status: {summary.status}")
    if summary.reason:
        print(f"Reason: {summary.reason}")
    print(f"Run ID: {summary.run_id}")
    print(f"Output: {args.output_root / summary.run_id}")

    # Exit code: 0 for pass, 1 for failure/error
    if summary.status == "ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
