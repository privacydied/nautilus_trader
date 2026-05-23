from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .hyperliquid_observer import DEFAULT_CHANNELS
from .hyperliquid_observer import DEFAULT_COINS
from .hyperliquid_observer import ObserverConfig
from .hyperliquid_observer import parse_csv
from .hyperliquid_observer import run_observer


def parse_channels(value: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in value.split(",") if x.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run public Hyperliquid market-data observer; writes hourly Parquet shards.")
    parser.add_argument("--coins", default=",".join(DEFAULT_COINS))
    parser.add_argument("--channels", default=",".join(DEFAULT_CHANNELS))
    parser.add_argument("--book-depth", type=int, default=20)
    parser.add_argument("--book-snapshot-interval-seconds", type=float, default=1.0)
    parser.add_argument("--out", type=Path, default=Path("data/hyperliquid_live/v0"))
    parser.add_argument("--duration-seconds", type=int, default=0)
    parser.add_argument("--max-rss-gb", type=float, default=4.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = ObserverConfig(
        coins=parse_csv(args.coins),
        channels=parse_channels(args.channels),
        book_depth=args.book_depth,
        book_snapshot_interval_seconds=args.book_snapshot_interval_seconds,
        out=args.out,
        duration_seconds=args.duration_seconds,
        max_rss_gb=args.max_rss_gb,
    )
    asyncio.run(run_observer(config))


if __name__ == "__main__":
    main()
