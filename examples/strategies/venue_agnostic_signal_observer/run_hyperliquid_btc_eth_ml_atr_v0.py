#!/usr/bin/env python3
"""
CLI runner for Hyperliquid BTC/ETH ML+ATR v0 offline research diagnostic.

NOT live trading. NOT paper execution. NOT bot authorization.
Produces reproducible archive backtest artifacts and tests only.
"""

import argparse
import sys
from pathlib import Path

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
    run_pipeline,
)


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Hyperliquid BTC/ETH ML+ATR v0 — offline research diagnostic",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Required inputs
    p.add_argument("--bars-path", required=True, type=Path,
                   help="Path to hourly bars CSV or Parquet")
    p.add_argument("--funding-path", type=Path, default=None,
                   help="Path to hourly funding CSV or Parquet (required unless --no-funding)")

    # Output
    p.add_argument("--output-root", type=Path,
                   default=Path("reports/hyperliquid_btc_eth_ml_atr_v0"),
                   help="Root directory for report output")

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

    # Split thresholds
    p.add_argument("--min-train-rows", type=int, default=2000)
    p.add_argument("--min-validation-rows", type=int, default=500)
    p.add_argument("--min-test-rows", type=int, default=500)
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

    cfg = MlAtrConfig(
        split=SplitConfig(
            min_train_rows=args.min_train_rows,
            min_validation_rows=args.min_validation_rows,
            min_test_rows=args.min_test_rows,
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

    summary = run_pipeline(
        cfg=cfg,
        bars_path=args.bars_path,
        funding_path=args.funding_path,
        output_root=args.output_root,
        symbols=symbols,
    )

    print(f"Status: {summary.status}")
    if summary.reason:
        print(f"Reason: {summary.reason}")
    print(f"Run ID: {summary.run_id}")
    print(f"Output: {args.output_root / summary.run_id}")

    # Exit code: 0 for pass, 1 for failure/error
    if summary.status in ("ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE", "ML_ATR_V0_BACKTEST_READY"):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
