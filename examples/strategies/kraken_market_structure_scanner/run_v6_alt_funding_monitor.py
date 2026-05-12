#!/usr/bin/env python3
"""V6-C: Altcoin funding/basis anomaly monitor — entry point.

Observer-only. No orders. No API keys. No live execution.
"""
import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples.strategies.kraken_market_structure_scanner.funding_config import FundingConfig, SYMBOL_MAP
from examples.strategies.kraken_market_structure_scanner.funding_scanner_alt import AltFundingScanner


DEFAULT_ASSETS = ["SOL", "XRP", "DOGE", "LINK", "AVAX", "ADA", "SUI", "ARB", "OP", "APT"]


def main():
    parser = argparse.ArgumentParser(description="V6-C Altcoin Funding/Basis Anomaly Monitor")
    parser.add_argument(
        "--assets", nargs="+", default=DEFAULT_ASSETS,
        help="Assets to scan (default: %(default)s)"
    )
    parser.add_argument(
        "--perp-venues", nargs="+", default=["binance", "bybit"],
        help="Perp venues (default: %(default)s)"
    )
    parser.add_argument(
        "--spot-venues", nargs="+", default=["kraken"],
        help="Spot venues (default: %(default)s)"
    )
    parser.add_argument(
        "--duration-seconds", type=float, default=600.0,
        help="Scan duration in seconds (default: 600 = 10min)"
    )
    parser.add_argument(
        "--poll-interval-seconds", type=float, default=10.0,
        help="Seconds between polls (default: %(default)s)"
    )
    parser.add_argument(
        "--min-funding-apr", type=float, default=30.0,
        help="Minimum funding APR %% to consider as candidate (default: %(default)s)"
    )
    parser.add_argument(
        "--min-conservative-net-edge-bps", type=float, default=25.0,
        help="Min conservative net edge in bps (default: %(default)s)"
    )
    parser.add_argument(
        "--min-persistence-polls", type=int, default=3,
        help="Min consecutive polls for durable candidate (default: %(default)s)"
    )
    parser.add_argument(
        "--out", type=str, default="reports/v6_market_structure",
        help="Output directory (default: %(default)s)"
    )
    args = parser.parse_args()

    # Validate asset mappings (informative, not fatal)
    missing_spot = []
    for a in args.assets:
        m = SYMBOL_MAP.get(a, {})
        has_spot = bool(m.get("kraken_spot"))
        has_any_perp = any(bool(m.get(f"{v}_perp")) for v in args.perp_venues)
        if not has_spot or not has_any_perp:
            missing_spot.append(a)

    cfg = FundingConfig(
        assets=args.assets,
        spot_venues=args.spot_venues,
        perp_venues=args.perp_venues,
        poll_interval_seconds=args.poll_interval_seconds,
        duration_seconds=args.duration_seconds,
        min_funding_apr=args.min_funding_apr,
        min_net_edge_bps=args.min_conservative_net_edge_bps,
    )

    scanner = AltFundingScanner(cfg)

    # Override min_persistence_polls since FundingConfig doesn't have it natively
    cfg.min_persistence_polls = args.min_persistence_polls  # type: ignore

    print(f"V6-C Altcoin Funding/Basis Anomaly Monitor")
    print(f"  Assets: {', '.join(args.assets)}")
    scanned_assets = [a for a in args.assets if a in scanner.assets]
    print(f"  Scanned assets (mapped): {', '.join(scanned_assets)}")
    print(f"  Spot venues: {', '.join(args.spot_venues)}")
    print(f"  Perp venues: {', '.join(args.perp_venues)}")
    print(f"  Poll every {args.poll_interval_seconds}s, duration {args.duration_seconds}s")
    print(f"  Min funding APR: {args.min_funding_apr}%")
    print(f"  Min conservative net edge: {args.min_conservative_net_edge_bps}bps")
    print(f"  Min persistence polls: {args.min_persistence_polls}")
    print(f"  Output: {args.out}")
    if missing_spot:
        print(f"  Assets with incomplete mapping: {', '.join(missing_spot)}")
    print()

    scanner.stats["scan_start"] = int(time.time() * 1000)
    scanner.stats["scan_end"] = int(time.time() * 1000)

    summary, stats = scanner.run()
    scanner.stats["scan_end"] = int(time.time() * 1000)

    # Re-write summary with end time
    from examples.strategies.kraken_market_structure_scanner.funding_reports_alt import write_summary_alt
    summary_path = write_summary_alt(
        scanner.stats,
        Path(args.out) / "funding_basis_alt_observations.jsonl",
        Path(args.out) / "funding_basis_alt_candidates.jsonl",
        Path(args.out),
        cfg,
    )

    print(f"\nScan complete.")
    print(f"  Polls: {stats['polls']}")
    print(f"  Observations: {stats['observations']}")
    print(f"  Candidates: {stats['candidates']}")
    print(f"  Durable candidates: {stats['durable_candidates']}")
    print(f"\nSummary: {summary_path}")


if __name__ == "__main__":
    main()
