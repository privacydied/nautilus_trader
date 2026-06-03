#!/usr/bin/env python3
"""
CLI runner for Hyperliquid BTC/ETH ML+ATR Official S3 Bars v0.

Fetch official Hyperliquid historical fills from S3, aggregate to 1h bars.
NOT live trading. NOT exchange-connected. S3 archive only.
"""

import argparse
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent.parent.parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import S3BarsConfig, run_s3_bars


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Hyperliquid BTC/ETH ML+ATR Official S3 Bars v0",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--output-root", type=Path,
                   default=Path("reports/hyperliquid_btc_eth_ml_atr_official_s3_bars_v0"))
    p.add_argument("--run-id", type=str, default=None)
    p.add_argument("--start-date", type=str, default="2023-10-01")
    p.add_argument("--end-date", type=str, default="2026-05-27")
    p.add_argument("--symbol", action="append", choices=["BTC", "ETH"],
                   help="Symbols to include (repeatable). Default: BTC ETH")
    p.add_argument("--s3-prefix", action="append",
                   help="S3 prefixes to inspect (repeatable)")
    p.add_argument("--funding-root", type=Path,
                   default=Path("examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0"))
    p.add_argument("--max-download-gb", type=float, default=25.0)
    p.add_argument("--plan-only", action="store_true", default=False)
    p.add_argument("--sample-only", action="store_true", default=False)
    p.add_argument("--execute", action="store_true", default=False)
    p.add_argument("--keep-raw", action="store_true", default=False)
    p.add_argument("--request-payer", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--no-sign-request", action="store_true", default=False)
    p.add_argument("--max-objects", type=int, default=None)
    p.add_argument("--max-gap-hours", type=int, default=24)
    p.add_argument("--write-csv", action="store_true", default=True)
    p.add_argument("--write-parquet", action="store_true", default=True)
    p.add_argument("--dry-run", action="store_true", default=False)

    args = p.parse_args(argv)

    if args.symbol is None:
        args.symbol = ["BTC", "ETH"]

    # Require exactly one mode
    modes = sum([args.plan_only, args.sample_only, args.execute, args.dry_run])
    if modes != 1:
        p.error("Exactly one of --plan-only, --sample-only, --execute, or --dry-run is required")

    return args


def main(argv=None):
    args = _parse_args(argv)

    symbols = tuple(sorted(set(args.symbol)))
    unknown = set(symbols) - {"BTC", "ETH"}
    if unknown:
        print(f"ERROR: Unknown symbols: {unknown}. Only BTC and ETH are valid.", file=sys.stderr)
        sys.exit(1)

    from hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import DEFAULT_S3_PREFIXES
    s3_prefixes = tuple(args.s3_prefix) if args.s3_prefix else tuple(DEFAULT_S3_PREFIXES)

    cfg = S3BarsConfig(
        output_root=args.output_root,
        run_id=args.run_id,
        start_date=args.start_date,
        end_date=args.end_date,
        symbols=symbols,
        s3_prefixes=s3_prefixes,
        funding_root=args.funding_root,
        max_download_gb=args.max_download_gb,
        plan_only=args.plan_only,
        sample_only=args.sample_only,
        execute=args.execute,
        keep_raw=args.keep_raw,
        request_payer=args.request_payer,
        no_sign_request=args.no_sign_request,
        max_objects=args.max_objects,
        max_gap_hours=args.max_gap_hours,
        write_csv=args.write_csv,
        write_parquet=args.write_parquet,
        dry_run=args.dry_run,
    )

    summary = run_s3_bars(cfg)

    print(f"Status: {summary.status}")
    if summary.reason:
        print(f"Reason: {summary.reason}")
    print(f"Run ID: {summary.run_id}")
    if summary.plan:
        print(f"Objects: {len(summary.plan.objects)}")
        print(f"Total GiB: {summary.plan.total_gb:.2f}")
        print(f"Earliest date: {summary.plan.earliest_date}")

    # Exit code: 0 for success/plan, 1 for blocked
    blocked_prefixes = ("BLOCKED", "ERROR")
    sys.exit(0 if not any(summary.status.startswith(p) for p in blocked_prefixes) else 1)


if __name__ == "__main__":
    main()
