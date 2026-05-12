"""Download public OHLCV data for the lead-lag experiment.

Fetches 1-minute bars from Binance and Kraken for BTC, ETH, SOL and saves
them as CSVs that the sweep runner can consume.

Usage:
    uv run -m examples.strategies.venue_agnostic_signal_observer.data_download \
        --data-dir data/lead_lag \
        --days 30 \
        --interval 1m

This is slow on purpose — it paginates through the exchange APIs and
respects rate limits.  Run it once, then reuse the CSVs offline.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .data_adapters import download_public_klines, save_venue_csv

# Mapping from asset slug to venue-specific symbols
_BINANCE_SYMBOLS = {
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
    "SOL/USDT": "SOLUSDT",
}

_KRAKEN_SYMBOLS = {
    "BTC/USD": "XXBTZUSD",
    "ETH/USD": "XETHZUSD",
    "SOL/USD": "SOLUSD",
}

_COINBASE_SYMBOLS = {
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download public OHLCV data for lead-lag experiments."
    )
    p.add_argument(
        "--data-dir",
        type=str,
        default="data/lead_lag",
        help="Directory to save CSV files.",
    )
    p.add_argument(
        "--days",
        type=int,
        default=30,
        help="How many days of history to download.",
    )
    p.add_argument(
        "--interval",
        type=str,
        default="1m",
        choices=["1m", "5m", "15m", "1h"],
        help="Candle interval. 1m is recommended for lead-lag.",
    )
    p.add_argument(
        "--end-ts",
        type=str,
        default=None,
        help="End time as ISO 8601 string (default: now). "
             "E.g. '2024-12-01T00:00:00Z' for reproducible experiments.",
    )
    return p.parse_args(argv)


def main():
    args = parse_args()
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.end_ts:
        end_dt = datetime.fromisoformat(args.end_ts.replace("Z", "+00:00"))
    else:
        end_dt = datetime.now(timezone.utc)

    start_dt = end_dt - timedelta(days=args.days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    window_label = f"{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}"
    print(f"Window: {start_dt.isoformat()} → {end_dt.isoformat()} ({args.days} days)")
    print(f"Interval: {args.interval}")
    print(f"Data dir: {data_dir.absolute()}")

    tasks: list[tuple[str, str, str, dict]] = []

    # Binance tasks
    for asset, symbol in _BINANCE_SYMBOLS.items():
        csv_path = data_dir / f"binance_{asset.replace('/', '_').lower()}_{args.interval}_{window_label}.csv"
        if csv_path.exists():
            print(f"Skip {csv_path.name} (already exists)")
            continue
        tasks.append(("binance", asset, symbol, {}))

    # Kraken tasks
    for asset, symbol in _KRAKEN_SYMBOLS.items():
        csv_path = data_dir / f"kraken_{asset.replace('/', '_').lower()}_{args.interval}_{window_label}.csv"
        if csv_path.exists():
            print(f"Skip {csv_path.name} (already exists)")
            continue
        tasks.append(("kraken", asset, symbol, {}))

    # Coinbase tasks — skipped for now: legacy API caps at ~300 bars,
    # new Advanced Trade API requires authentication.
    # for asset, symbol in _COINBASE_SYMBOLS.items():
    #     csv_path = data_dir / f"coinbase_{asset.replace('/', '_').lower()}_{args.interval}_{window_label}.csv"
    #     if csv_path.exists():
    #         print(f"Skip {csv_path.name} (already exists)")
    #         continue
    #     tasks.append(("coinbase", asset, symbol, {}))

    if not tasks:
        print("All CSVs already exist. Nothing to download.")
        return

    print(f"\nDownloading {len(tasks)} datasets...")

    for venue, asset, symbol, kwargs in tasks:
        label = f"{venue}/{asset}"
        print(f"\n[{label}] Downloading {args.interval} bars...")
        try:
            klines = download_public_klines(
                venue=venue,
                symbol=symbol,
                interval=args.interval,
                start_ts=start_ms,
                end_ts=end_ms,
            )
            csv_path = data_dir / f"{venue}_{asset.replace('/', '_').lower()}_{args.interval}_{window_label}.csv"
            save_venue_csv(str(csv_path), klines)
            print(f"[{label}] Saved {len(klines)} rows → {csv_path}")
        except Exception as e:
            print(f"[{label}] ERROR: {e}")
            continue

    print(f"\nDone. Data directory: {data_dir.absolute()}")


if __name__ == "__main__":
    main()
