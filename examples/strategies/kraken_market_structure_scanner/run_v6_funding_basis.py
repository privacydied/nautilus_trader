#!/usr/bin/env python3
"""V6-B: Funding/Basis scanner — entry point."""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples.strategies.kraken_market_structure_scanner.funding_config import FundingConfig, SYMBOL_MAP
from examples.strategies.kraken_market_structure_scanner.funding_scanner import run_funding_scan
from examples.strategies.kraken_market_structure_scanner.symbols import STANDARD_SYMBOLS


def main():
    parser = argparse.ArgumentParser(description="V6-B Funding/Basis Scanner")
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--spot-venues", nargs="+", default=["kraken"])
    parser.add_argument("--perp-venues", nargs="+", default=["kraken", "binance", "bybit"])
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--min-funding-apr", type=float, default=20.0)
    parser.add_argument("--min-net-edge-bps", type=float, default=25.0)
    parser.add_argument("--spot-fee-bps", type=float, default=40.0)
    parser.add_argument("--perp-fee-bps", type=float, default=40.0)
    parser.add_argument("--out", type=str, default="reports/v6_market_structure")
    args = parser.parse_args()

    for a in args.assets:
        if a not in SYMBOL_MAP:
            print(f"Error: unknown asset '{a}'. Known: {list(SYMBOL_MAP.keys())}")
            sys.exit(1)

    cfg = FundingConfig(
        assets=args.assets,
        spot_venues=args.spot_venues,
        perp_venues=args.perp_venues,
        poll_interval_seconds=args.poll_interval_seconds,
        duration_seconds=args.duration_seconds,
        min_funding_apr=args.min_funding_apr,
        min_net_edge_bps=args.min_net_edge_bps,
        spot_taker_fee_bps=args.spot_fee_bps,
        perp_taker_fee_bps=args.perp_fee_bps,
        output_dir=args.out,
    )

    print(f"V6-B Funding/Basis Scanner: {', '.join(args.assets)}")
    print(f"  Spot venues: {', '.join(args.spot_venues)}")
    print(f"  Perp venues: {', '.join(args.perp_venues)}")
    print(f"  Poll every {args.poll_interval_seconds}s, duration {args.duration_seconds}s")
    print(f"  Fees: spot={args.spot_fee_bps}bps perp={args.perp_fee_bps}bps")
    print(f"  Min funding APR: {args.min_funding_apr}%")
    print(f"  Min net edge: {args.min_net_edge_bps}bps")
    print()

    summary_path, stats = run_funding_scan(cfg)

    print(f"\nDone. {stats['polls']} polls, {stats['observations']} observations, {stats['candidates']} candidates.")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
