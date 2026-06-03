#!/usr/bin/env python3
"""
CLI runner for Hyperliquid BTC/ETH ML+ATR Data Prep v0.

Deterministic local trade-JSONL → 1h OHLCV bar builder.
NOT live trading. NOT network-connected. Local files only.
"""

import argparse
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent.parent.parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from hyperliquid_btc_eth_ml_atr_data_prep_v0 import DataPrepConfig, run_data_prep


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Hyperliquid BTC/ETH ML+ATR Data Prep v0 — local trade → 1h bars",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--trade-root", action="append", type=Path, default=[],
                   help="Root directories to discover trade JSONL files")
    p.add_argument("--trade-path", action="append", type=Path, default=[],
                   help="Specific trade JSONL files")
    p.add_argument("--funding-root", action="append", type=Path, default=[],
                   help="Root directories to discover funding JSONL files")
    p.add_argument("--funding-path", action="append", type=Path, default=[],
                   help="Specific funding JSONL files")
    p.add_argument("--output-root", type=Path,
                   default=Path("reports/hyperliquid_btc_eth_ml_atr_data_prep_v0"))
    p.add_argument("--run-id", type=str, default=None)
    p.add_argument("--symbol", action="append", choices=["BTC", "ETH"],
                   help="Symbols to include (repeatable). Default: BTC ETH")
    p.add_argument("--input-kind", choices=["auto", "trades", "bars"], default="auto")
    p.add_argument("--max-gap-hours", type=int, default=24)
    p.add_argument("--max-abs-funding-rate", type=float, default=0.01)
    p.add_argument("--allow-zero-volume-bars", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--write-csv", action="store_true", default=True)
    p.add_argument("--write-parquet", action="store_true", default=True)
    p.add_argument("--dry-run", action="store_true", default=False)

    args = p.parse_args(argv)

    if args.symbol is None:
        args.symbol = ["BTC", "ETH"]

    return args


def main(argv=None):
    args = _parse_args(argv)

    symbols = tuple(sorted(set(args.symbol)))
    unknown = set(symbols) - {"BTC", "ETH"}
    if unknown:
        print(f"ERROR: Unknown symbols: {unknown}. Only BTC and ETH are valid.", file=sys.stderr)
        sys.exit(1)

    # Default roots
    default_trade_root = Path("examples/strategies/venue_agnostic_signal_observer/data")
    default_funding_root = Path("examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0")

    trade_roots = args.trade_root if args.trade_root else [default_trade_root]
    funding_roots = args.funding_root if args.funding_root else [default_funding_root]

    cfg = DataPrepConfig(
        trade_roots=tuple(trade_roots),
        trade_paths=tuple(args.trade_path),
        funding_roots=tuple(funding_roots),
        funding_paths=tuple(args.funding_path),
        output_root=args.output_root,
        run_id=args.run_id,
        symbols=symbols,
        input_kind=args.input_kind,
        max_gap_hours=args.max_gap_hours,
        max_abs_funding_rate=args.max_abs_funding_rate,
        allow_zero_volume_bars=args.allow_zero_volume_bars,
        write_csv=args.write_csv,
        write_parquet=args.write_parquet,
        dry_run=args.dry_run,
    )

    summary = run_data_prep(cfg)

    print(f"Status: {summary.status}")
    if summary.reason:
        print(f"Reason: {summary.reason}")
    print(f"Run ID: {summary.run_id}")
    if summary.bars_summary:
        print(f"Bars: {summary.bars_summary.bars_row_count} rows")
    if summary.funding_summary:
        print(f"Funding: {summary.funding_summary.funding_row_count} rows")

    sys.exit(0 if summary.status == "DATA_PREP_V0_READY_FOR_ML_ATR" else 1)


if __name__ == "__main__":
    main()
