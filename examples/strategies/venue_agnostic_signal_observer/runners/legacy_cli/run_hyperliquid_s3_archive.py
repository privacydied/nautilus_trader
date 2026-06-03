from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import S3ProbeUnavailable
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import estimate_as_dict
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import estimate_s3_cost
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_s3_archive import fetch_s3_archive


def _read_date_list(path: Path) -> list[date]:
    dates: list[date] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        dates.append(date.fromisoformat(line))
    if not dates:
        raise ValueError(f"date list is empty: {path}")
    return sorted(set(dates))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate or fetch Hyperliquid requester-pays S3 archive. Default is estimate-only; bulk transfer needs --confirm-s3-spend."
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="Print estimate only; no bulk S3 transfer. Default unless --confirm-s3-spend is supplied.",
    )
    parser.add_argument("--confirm-s3-spend", default="", help="Required for bulk transfer: I_HAVE_BUDGET_<estimated_usd>")
    parser.add_argument("--coins", default="BTC,ETH,SOL,LINK,DOGE,AVAX")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--date-list", type=Path, help="Newline-separated YYYY-MM-DD dates; replaces --start-date/--end-date.")
    parser.add_argument("--max-usd-budget", type=float, default=25.0)
    parser.add_argument("--out", type=Path, default=Path("data/hyperliquid_archive/v0"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    coins = [x.strip().upper() for x in args.coins.split(",") if x.strip()]
    if args.date_list:
        dates = _read_date_list(args.date_list)
        start = dates[0]
        end = dates[-1]
    else:
        if not args.start_date or not args.end_date:
            raise SystemExit("--start-date/--end-date are required unless --date-list is supplied")
        dates = None
        start = date.fromisoformat(args.start_date)
        end = date.fromisoformat(args.end_date)
    try:
        estimate = estimate_s3_cost(coins, start, end, dates=dates)
    except S3ProbeUnavailable as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2) from exc
    estimate_mode = args.estimate_only or not args.confirm_s3_spend
    if estimate_mode:
        print(json.dumps(estimate_as_dict(estimate), indent=2, sort_keys=True), flush=True)
        return
    result = fetch_s3_archive(coins, start, end, args.out, args.max_usd_budget, args.confirm_s3_spend, dates=dates)
    print(json.dumps(result, default=lambda o: o.__dict__, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
