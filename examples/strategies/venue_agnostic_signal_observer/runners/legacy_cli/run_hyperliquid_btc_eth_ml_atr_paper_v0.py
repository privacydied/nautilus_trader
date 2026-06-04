#!/usr/bin/env python3
"""
CLI runner for Hyperliquid BTC/ETH ML+ATR Paper v0 — simulated-paper diagnostic (Task A).

NOT live trading. NOT paper broker execution. NOT bot authorization.
Local file-fed simulated-paper ledger only.
"""

import argparse
from ._prog import set_legacy_prog
import sys
from pathlib import Path

# File moved under runners/legacy_cli; the paired algorithm module still lives
# at the package root, so resolve sys.path to the package root (parents[2]).
_here = Path(__file__).resolve().parents[2]
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from hyperliquid_btc_eth_ml_atr_paper_v0 import (
    PaperConfig,
    load_model_bundle,
    validate_model_bundle,
    simulate_paper_once,
)


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Hyperliquid BTC/ETH ML+ATR Paper v0 — simulated-paper diagnostic (Task A)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    set_legacy_prog(p)

    p.add_argument("--model-bundle", required=True, type=Path)
    p.add_argument("--bars-path", required=True, type=Path)
    p.add_argument("--funding-path", type=Path, default=None)
    p.add_argument("--output-root", type=Path, default=Path("reports/hyperliquid_btc_eth_ml_atr_paper_v0"))
    p.add_argument("--paper-run-id", type=str, default=None)
    p.add_argument("--state-path", type=Path, default=None)
    p.add_argument("--symbol", action="append", choices=["BTC", "ETH"],
                   help="Symbols to include (repeatable). Default: BTC ETH")
    p.add_argument("--once", action="store_true", required=True,
                   help="Required for Task A: run once and exit")
    p.add_argument("--close-open-at-end", action="store_true", default=False)
    p.add_argument("--strict-funding", action="store_true", default=False)
    p.add_argument("--no-funding", action="store_true", default=False)
    p.add_argument("--allow-zero-volume-bars", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--max-gap-hours", type=int, default=24)
    p.add_argument("--allow-nonpassing-bundle-for-test-fixtures", action="store_true", default=False)
    p.add_argument("--equivalence-check", choices=["strict", "warn", "off"], default="warn")
    p.add_argument("--position-update-events", choices=["every_bar", "on_change", "disabled"],
                   default="every_bar")
    p.add_argument("--state-recovery", choices=["strict", "permissive"], default="strict")
    p.add_argument("--i-know-what-i-am-doing", action="store_true", default=False)
    p.add_argument("--dry-run", action="store_true", default=False)

    args = p.parse_args(argv)

    if args.funding_path is None and not args.no_funding:
        p.error("--funding-path is required unless --no-funding is set")
    if args.state_recovery == "permissive" and not args.i_know_what_i_am_doing:
        p.error("--state-recovery permissive requires --i-know-what-i-am-doing")
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

    # Load and validate bundle
    try:
        bundle = load_model_bundle(args.model_bundle)
        validate_model_bundle(
            bundle,
            allow_nonpassing_bundle_for_test_fixtures=args.allow_nonpassing_bundle_for_test_fixtures,
        )
    except ValueError as e:
        print(f"BUNDLE ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    cfg = PaperConfig(
        model_bundle_path=args.model_bundle,
        bars_path=args.bars_path,
        funding_path=args.funding_path,
        output_root=args.output_root,
        paper_run_id=args.paper_run_id,
        state_path=args.state_path,
        symbols=symbols,
        close_open_at_end=args.close_open_at_end,
        strict_funding=args.strict_funding,
        no_funding=args.no_funding,
        allow_zero_volume_bars=args.allow_zero_volume_bars,
        max_gap_hours=args.max_gap_hours,
        allow_nonpassing_bundle_for_test_fixtures=args.allow_nonpassing_bundle_for_test_fixtures,
        equivalence_check=args.equivalence_check,
        position_update_events=args.position_update_events,
        state_recovery=args.state_recovery,
        dry_run=args.dry_run,
        i_know_what_i_am_doing=args.i_know_what_i_am_doing,
    )

    # Load bars
    import pandas as pd
    if args.bars_path.suffix == ".parquet":
        bars = pd.read_parquet(args.bars_path)
    else:
        bars = pd.read_csv(args.bars_path)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    bars = bars[bars["symbol"].isin(symbols)]

    # Load funding
    funding_df = None
    if not args.no_funding and args.funding_path:
        if args.funding_path.suffix == ".parquet":
            funding_df = pd.read_parquet(args.funding_path)
        else:
            funding_df = pd.read_csv(args.funding_path)
        funding_df["timestamp"] = pd.to_datetime(funding_df["timestamp"], utc=True)

    output_dir = args.output_root / (args.paper_run_id or "default")

    summary = simulate_paper_once(cfg, bundle, bars, funding_df, output_dir)

    print(f"Status: {summary.status}")
    print(f"StatusKind: {summary.status_kind}")
    if summary.reason:
        print(f"Reason: {summary.reason}")
    print(f"Paper Run ID: {summary.paper_run_id}")
    print(f"Closed trades: {summary.closed_trade_count}")
    print(f"Cumulative net bps: {summary.cumulative_net_bps:.4f}")
    print(f"Output: {output_dir}")

    sys.exit(0 if summary.status_kind == STATUS_KIND_FINAL else 1)


if __name__ == "__main__":
    from hyperliquid_btc_eth_ml_atr_paper_v0 import STATUS_KIND_FINAL
    main()
