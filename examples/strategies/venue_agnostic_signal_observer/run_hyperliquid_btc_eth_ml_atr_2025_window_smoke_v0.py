"""Minimal diagnostic runner for 2025 window smoke v0 - Stage A representative validation.

This script validates the data pipeline end-to-end:
1. Load bars from parquet
2. Split by representative windows
3. Validate row counts against representative gates
4. Compute basic features (returns, volatility)
5. Report status

Does NOT train ML models or run full backtest - just validates data quality and pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np


# Representative-mode statuses
ALLOWED_STATUSES = {
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_PLAN_READY",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_PIPELINE_VALIDATED",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_NEEDS_MORE_DATA",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_COST_OR_SIZE_CAP",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_INSUFFICIENT_DISK",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_PARSE_FAILED",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DRY_RUN_FAILED",
    "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DIAGNOSTIC_FAILED",
}

FORBIDDEN_STATUSES = {
    "SHADOW_LOGGING_ELIGIBLE",
    "PAPER_SIM_V0",
    "TRADE_READY",
    "EXECUTION_READY",
    "LIVE_READY",
    "CANDIDATE_FOR_LIVE",
    "PROFITABLE",
}


def load_bars(bars_path: Path) -> pd.DataFrame:
    """Load bars from parquet file."""
    bars = pd.read_parquet(bars_path)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    return bars


def split_data(
    bars: pd.DataFrame,
    train_start: str,
    train_end: str,
    validation_start: str,
    validation_end: str,
    test_start: str,
    test_end: str,
) -> Dict[str, pd.DataFrame]:
    """Split bars into train/val/test sets."""
    splits = {}
    
    train_start_dt = pd.Timestamp(train_start, tz="UTC")
    train_end_dt = pd.Timestamp(train_end, tz="UTC")
    val_start_dt = pd.Timestamp(validation_start, tz="UTC")
    val_end_dt = pd.Timestamp(validation_end, tz="UTC")
    test_start_dt = pd.Timestamp(test_start, tz="UTC")
    test_end_dt = pd.Timestamp(test_end, tz="UTC")
    
    for symbol in bars["symbol"].unique():
        sym_bars = bars[bars["symbol"] == symbol].sort_values("timestamp")
        
        splits[f"{symbol}_train"] = sym_bars[
            (sym_bars["timestamp"] >= train_start_dt) & 
            (sym_bars["timestamp"] <= train_end_dt)
        ]
        splits[f"{symbol}_validation"] = sym_bars[
            (sym_bars["timestamp"] >= val_start_dt) & 
            (sym_bars["timestamp"] <= val_end_dt)
        ]
        splits[f"{symbol}_test"] = sym_bars[
            (sym_bars["timestamp"] >= test_start_dt) & 
            (sym_bars["timestamp"] <= test_end_dt)
        ]
    
    return splits


def validate_row_counts(
    splits: Dict[str, pd.DataFrame],
    min_train_rows: int,
    min_validation_rows: int,
    min_test_rows: int,
) -> tuple[bool, Dict[str, Any]]:
    """Validate row counts against gates."""
    validation = {
        "gates": {
            "min_train_rows": min_train_rows,
            "min_validation_rows": min_validation_rows,
            "min_test_rows": min_test_rows,
        },
        "actual": {},
        "passed": True,
        "failures": [],
    }
    
    for symbol in ["BTC", "ETH"]:
        train_rows = len(splits.get(f"{symbol}_train", []))
        val_rows = len(splits.get(f"{symbol}_validation", []))
        test_rows = len(splits.get(f"{symbol}_test", []))
        
        validation["actual"][symbol] = {
            "train_rows": train_rows,
            "validation_rows": val_rows,
            "test_rows": test_rows,
        }
        
        if train_rows < min_train_rows:
            validation["passed"] = False
            validation["failures"].append(f"{symbol} train: {train_rows} < {min_train_rows}")
        
        if val_rows < min_validation_rows:
            validation["passed"] = False
            validation["failures"].append(f"{symbol} validation: {val_rows} < {min_validation_rows}")
        
        if test_rows < min_test_rows:
            validation["passed"] = False
            validation["failures"].append(f"{symbol} test: {test_rows} < {min_test_rows}")
    
    return validation["passed"], validation


def compute_basic_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute minimal features for validation."""
    bars = bars.copy()
    bars = bars.sort_values(["symbol", "timestamp"])
    
    # Simple returns
    bars["ret_1h"] = bars.groupby("symbol")["close"].pct_change()
    bars["ret_4h"] = bars.groupby("symbol")["close"].pct_change(4)
    bars["ret_24h"] = bars.groupby("symbol")["close"].pct_change(24)
    
    # Realized volatility (rolling std of returns)
    bars["realized_vol_24h"] = bars.groupby("symbol")["ret_1h"].transform(
        lambda x: x.rolling(24, min_periods=1).std()
    )
    
    return bars


def run_diagnostic(
    bars_path: Path,
    output_root: Path,
    run_id: str,
    train_start: str,
    train_end: str,
    validation_start: str,
    validation_end: str,
    test_start: str,
    test_end: str,
    min_train_rows: int,
    min_validation_rows: int,
    min_test_rows: int,
) -> Dict[str, Any]:
    """Run the representative diagnostic."""
    result = {
        "run_id": run_id,
        "bars_path": str(bars_path),
        "output_root": str(output_root),
        "representative_mode": True,
        "status": None,
        "validation": None,
        "features_computed": False,
        "errors": [],
    }
    
    # Load bars
    print(f"Loading bars from {bars_path}...")
    try:
        bars = load_bars(bars_path)
        print(f"  Loaded {len(bars)} rows")
    except Exception as e:
        result["status"] = "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_PARSE_FAILED"
        result["errors"].append(f"Failed to load bars: {e}")
        return result
    
    # Split data
    print(f"Splitting data into train/val/test...")
    try:
        splits = split_data(
            bars,
            train_start, train_end,
            validation_start, validation_end,
            test_start, test_end,
        )
        print(f"  Split complete")
    except Exception as e:
        result["status"] = "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DIAGNOSTIC_FAILED"
        result["errors"].append(f"Failed to split data: {e}")
        return result
    
    # Validate row counts
    print(f"Validating row counts against representative gates...")
    passed, validation = validate_row_counts(
        splits,
        min_train_rows,
        min_validation_rows,
        min_test_rows,
    )
    result["validation"] = validation
    
    if not passed:
        print(f"  FAILED: {validation['failures']}")
        result["status"] = "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_NEEDS_MORE_DATA"
        result["errors"].extend(validation["failures"])
        return result
    
    print(f"  PASSED")
    
    # Compute features
    print(f"Computing basic features...")
    try:
        bars_with_features = compute_basic_features(bars)
        result["features_computed"] = True
        print(f"  Features computed")
    except Exception as e:
        result["status"] = "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DIAGNOSTIC_FAILED"
        result["errors"].append(f"Failed to compute features: {e}")
        return result
    
    # All checks passed
    result["status"] = "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_PIPELINE_VALIDATED"
    
    # Write outputs
    output_dir = output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Write summary.json
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(f"Wrote summary: {summary_path}")
    
    # Write summary.md
    md_lines = [
        "# Representative Diagnostic Summary",
        "",
        f"**Run ID**: {run_id}",
        f"**Status**: `{result['status']}`",
        "",
        "## Data Split",
        "",
    ]
    for symbol in ["BTC", "ETH"]:
        actual = validation["actual"].get(symbol, {})
        md_lines.append(f"### {symbol}")
        md_lines.append(f"- Train rows: {actual.get('train_rows', 'N/A')} (min: {min_train_rows})")
        md_lines.append(f"- Validation rows: {actual.get('validation_rows', 'N/A')} (min: {min_validation_rows})")
        md_lines.append(f"- Test rows: {actual.get('test_rows', 'N/A')} (min: {min_test_rows})")
        md_lines.append("")
    
    if validation["passed"]:
        md_lines.append("**Result**: Representative pipeline validated ✅")
    else:
        md_lines.append("**Result**: Needs more data ❌")
        md_lines.append("")
        md_lines.append("Failures:")
        for failure in validation["failures"]:
            md_lines.append(f"- {failure}")
    
    md_path = output_dir / "summary.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines) + "\n")
    print(f"Wrote summary markdown: {md_path}")
    
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="2025 Window Smoke v0 Representative Diagnostic")
    parser.add_argument("--bars-path", type=Path, required=True,
                        help="Path to bars parquet file")
    parser.add_argument("--output-root", type=Path, required=True,
                        help="Output root directory")
    parser.add_argument("--run-id", required=True,
                        help="Unique run identifier")
    parser.add_argument("--train-start", default="2025-08-01",
                        help="Train start date")
    parser.add_argument("--train-end", default="2025-08-31",
                        help="Train end date")
    parser.add_argument("--validation-start", default="2026-01-01",
                        help="Validation start date")
    parser.add_argument("--validation-end", default="2026-01-31",
                        help="Validation end date")
    parser.add_argument("--test-start", default="2026-03-01",
                        help="Test start date")
    parser.add_argument("--test-end", default="2026-03-31",
                        help="Test end date")
    parser.add_argument("--min-train-rows-per-symbol", type=int, default=500,
                        help="Minimum train rows per symbol")
    parser.add_argument("--min-validation-rows-per-symbol", type=int, default=300,
                        help="Minimum validation rows per symbol")
    parser.add_argument("--min-test-rows-per-symbol", type=int, default=300,
                        help="Minimum test rows per symbol")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry run mode (skip feature computation)")
    
    args = parser.parse_args()
    
    if args.dry_run:
        print("DRY RUN MODE - skipping feature computation")
        result = {
            "run_id": args.run_id,
            "dry_run": True,
            "status": "ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DRY_RUN_PASSED",
        }
        output_dir = args.output_root / args.run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / "summary.json"
        with open(summary_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Dry run summary: {summary_path}")
        print(f"STATUS: {result['status']}")
        return
    
    result = run_diagnostic(
        bars_path=args.bars_path,
        output_root=args.output_root,
        run_id=args.run_id,
        train_start=args.train_start,
        train_end=args.train_end,
        validation_start=args.validation_start,
        validation_end=args.validation_end,
        test_start=args.test_start,
        test_end=args.test_end,
        min_train_rows=args.min_train_rows_per_symbol,
        min_validation_rows=args.min_validation_rows_per_symbol,
        min_test_rows=args.min_test_rows_per_symbol,
    )
    
    print(f"\n{'='*60}")
    print(f"FINAL STATUS: {result['status']}")
    print(f"{'='*60}")
    
    if result["errors"]:
        print("\nErrors:")
        for err in result["errors"]:
            print(f"  - {err}")
    
    # Exit with appropriate code
    if "VALIDATED" in result["status"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()