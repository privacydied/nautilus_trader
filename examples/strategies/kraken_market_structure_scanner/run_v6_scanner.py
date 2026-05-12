#!/usr/bin/env python3
"""V6: Market Structure Scanner — entry point."""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples.strategies.kraken_market_structure_scanner.config import ScannerConfig, FeeConfig
from examples.strategies.kraken_market_structure_scanner.symbols import get_spec, STANDARD_SYMBOLS
from examples.strategies.kraken_market_structure_scanner.scanner import Scanner


def main():
    parser = argparse.ArgumentParser(description="V6 Market Structure Scanner")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USD", "ETH/USD"])
    parser.add_argument("--venues", nargs="+", default=["kraken", "coinbase"])
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--min-net-edge-bps", type=float, default=0.0)
    parser.add_argument("--latency-buffer-bps", type=float, default=10.0)
    parser.add_argument("--kraken-fee-bps", type=float, default=40.0)
    parser.add_argument("--coinbase-fee-bps", type=float, default=40.0)
    parser.add_argument("--binance-fee-bps", type=float, default=10.0)
    parser.add_argument("--out", type=str, default="reports/v6_market_structure")
    parser.add_argument("--log-all", action="store_true", help="Log all opportunities including negative edge")
    args = parser.parse_args()

    # Validate symbols
    for s in args.symbols:
        if s not in STANDARD_SYMBOLS:
            print(f"Error: unknown symbol '{s}'. Known: {list(STANDARD_SYMBOLS.keys())}")
            sys.exit(1)

    # Build configs
    cfg = ScannerConfig(
        symbols=args.symbols,
        venues=args.venues,
        poll_interval_seconds=args.poll_interval_seconds,
        min_net_edge_bps=args.min_net_edge_bps,
        latency_buffer_bps=args.latency_buffer_bps,
        max_runtime_seconds=args.duration_seconds,
        output_dir=args.out,
    )
    fees = FeeConfig(
        kraken=args.kraken_fee_bps,
        coinbase=args.coinbase_fee_bps,
        binance=args.binance_fee_bps,
    )

    print(f"V6 Scanner: {', '.join(args.symbols)} on {', '.join(args.venues)}")
    print(f"  Poll every {args.poll_interval_seconds}s, duration {args.duration_seconds}s")
    print(f"  Fees: kraken={args.kraken_fee_bps}bps coinbase={args.coinbase_fee_bps}bps binance={args.binance_fee_bps}bps")
    print(f"  Latency buffer: {args.latency_buffer_bps}bps")
    print(f"  Output: {args.out}")
    print()

    scanner = Scanner(cfg, fees)
    summary = scanner.run(args.out, args.duration_seconds, log_all=args.log_all)

    print(f"\nDone. {summary['total_polls']} polls, {summary['total_opportunities']} opportunities logged.")
    if summary['max_net_edge_bps'] is not None:
        print(f"Max net edge: {summary['max_net_edge_bps']:.2f} bps")
    print(f"Summary written to {args.out}/summary.json")


if __name__ == "__main__":
    main()
