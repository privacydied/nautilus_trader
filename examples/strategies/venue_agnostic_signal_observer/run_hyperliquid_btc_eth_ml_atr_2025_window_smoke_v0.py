#!/usr/bin/env python3
"""
CLI runner for the 2025-window smoke diagnostic.

Usage:
    uv run --no-sync -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 \\
        --bars-path /path/to/bars.parquet \\
        --funding-path /path/to/funding.parquet \\
        --output-root reports/hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 \\
        --run-id smoke_run_001 \\
        --symbol BTC \\
        --symbol ETH
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0 import (
    MLATR2025WindowConfig,
    SplitConfig2025,
    ModelConfig2025,
    run_2025_window_smoke_diagnostic,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the 2025-window ML+ATR smoke diagnostic"
    )
    parser.add_argument("--bars-path", required=True, help="Path to 1h bars parquet/csv")
    parser.add_argument("--funding-path", required=True, help="Path to funding parquet/csv")
    parser.add_argument("--output-root", default="reports/hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0",
                        help="Output root directory")
    parser.add_argument("--run-id", default=None, help="Run ID (auto-generated if not provided)")
    parser.add_argument("--symbol", action="append", default=["BTC", "ETH"],
                        choices=["BTC", "ETH"], help="Symbols to include")
    parser.add_argument("--train-start", default="2025-08-01", help="Train start date")
    parser.add_argument("--train-end", default="2025-12-31", help="Train end date")
    parser.add_argument("--validation-start", default="2026-01-01", help="Validation start date")
    parser.add_argument("--validation-end", default="2026-02-28", help="Validation end date")
    parser.add_argument("--test-start", default="2026-03-01", help="Test start date")
    parser.add_argument("--min-train-rows", type=int, default=3000, help="Min train rows per symbol")
    parser.add_argument("--min-validation-rows", type=int, default=1000, help="Min validation rows per symbol")
    parser.add_argument("--min-test-rows", type=int, default=1000, help="Min test rows per symbol")
    parser.add_argument("--label-horizon-bars", type=int, default=24, help="Label horizon in bars")
    parser.add_argument("--long-threshold", type=float, default=0.55, help="Long threshold")
    parser.add_argument("--short-threshold", type=float, default=0.40, help="Short threshold")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--model-backend", default="auto", choices=["auto", "sklearn", "numpy_fallback"],
                        help="Model backend")
    parser.add_argument("--calibrator", default="platt", choices=["platt", "isotonic"],
                        help="Calibrator")
    parser.add_argument("--max-gap-hours", type=int, default=24, help="Max gap hours")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs but do not fit model")
    return parser.parse_args()


def main():
    args = parse_args()

    bars_path = Path(args.bars_path)
    if not bars_path.exists():
        logger.error(f"Bars path not found: {bars_path}")
        sys.exit(1)

    funding_path = Path(args.funding_path) if args.funding_path else None
    if funding_path and not funding_path.exists():
        logger.error(f"Funding path not found: {funding_path}")
        sys.exit(1)

    cfg = MLATR2025WindowConfig(
        split=SplitConfig2025(
            train_start=f"{args.train_start}T00:00:00Z",
            train_end=f"{args.train_end}T23:59:59Z",
            validation_start=f"{args.validation_start}T00:00:00Z",
            validation_end=f"{args.validation_end}T23:59:59Z",
            test_start=f"{args.test_start}T00:00:00Z",
            min_train_rows=args.min_train_rows,
            min_validation_rows=args.min_validation_rows,
            min_test_rows=args.min_test_rows,
        ),
        model=ModelConfig2025(
            long_threshold=args.long_threshold,
            short_threshold=args.short_threshold,
            model_backend=args.model_backend,
            calibrator=args.calibrator,
            seed=args.seed,
            max_gap_hours=args.max_gap_hours,
        ),
        symbols=tuple(args.symbol),
        dry_run=args.dry_run,
    )

    output_root = Path(args.output_root)
    if args.run_id:
        # Use custom run_id by temporarily setting it
        output_root = output_root / args.run_id

    logger.info(f"Starting 2025-window smoke diagnostic")
    logger.info(f"Bars: {bars_path}")
    logger.info(f"Funding: {funding_path}")
    logger.info(f"Symbols: {cfg.symbols}")
    logger.info(f"Split: train={cfg.split.train_start} to {cfg.split.train_end}, "
                f"val={cfg.split.validation_start} to {cfg.split.validation_end}, "
                f"test={cfg.split.test_start}+")
    logger.info(f"Dry run: {cfg.dry_run}")

    summary = run_2025_window_smoke_diagnostic(
        cfg=cfg,
        bars_path=bars_path,
        funding_path=funding_path,
        output_root=Path(args.output_root),
        symbols=cfg.symbols,
    )

    print(f"\n{'='*60}")
    print(f"Status: {summary.status}")
    if summary.reason:
        print(f"Reason: {summary.reason}")
    print(f"Train rows: {summary.train_rows}")
    print(f"Validation rows: {summary.validation_rows}")
    print(f"Test rows: {summary.test_rows}")
    print(f"Train AUC: {summary.train_auc:.4f}")
    print(f"Validation AUC: {summary.validation_auc:.4f}")
    if summary.test_metrics:
        m = summary.test_metrics
        print(f"Test trades: {m.total_trades}")
        print(f"Test long trades: {m.long_trades}")
        print(f"Test short trades: {m.short_trades}")
        print(f"Test mean net bps: {m.mean_net_bps:.4f}")
        print(f"Test median net bps: {m.median_net_bps:.4f}")
        print(f"Test win rate: {m.win_rate:.4f}")
        print(f"Test profit factor: {m.profit_factor:.4f}")
    if summary.calibration:
        c = summary.calibration
        print(f"Calibration method: {c.method}")
        print(f"Test Brier: {c.test_brier:.6f}")
        print(f"Test ECE: {c.test_ece:.6f}")
    if summary.warnings:
        print(f"Warnings: {len(summary.warnings)}")
        for w in summary.warnings:
            print(f"  - {w}")
    print(f"{'='*60}")

    if summary.status == "ML_ATR_2025_WINDOW_SMOKE_V0_BACKTEST_COMPLETE_PIPELINE_VALIDATED":
        print("PIPELINE VALIDATED — signals are non-degenerate and economically viable.")
    elif summary.status == "ML_ATR_2025_WINDOW_SMOKE_V0_SIGNAL_PRESENT_NOT_PROMOTABLE":
        print("Signal present but not promotable.")
    elif summary.status == "ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA":
        print("NEEDS MORE DATA — insufficient coverage.")
    else:
        print(f"Status: {summary.status}")

    return summary


if __name__ == "__main__":
    main()
