#!/usr/bin/env python3
"""
V5: Download historical OHLCV data from Kraken public API for multiple pairs.

Downloads 4h (240m) or daily (1440m) bars for all liquid Kraken USD pairs.

Usage:
    python download_kraken_multi.py --output-dir data/kraken_4h/ --interval 240 --since 2024-01-01 --until 2026-05-01
    python download_kraken_multi.py --pair BTC/USD --interval 1440 --since 2024-01-01
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

import requests

KRAKEN_API_URL = "https://api.kraken.com/0/public/OHLC"
REQUEST_DELAY = 2

DEFAULT_PAIRS = [
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD",
    "LINK/USD", "DOGE/USD", "AVAX/USD", "LTC/USD", "BCH/USD",
]

DEFAULT_INTERVALS = [240, 1440]


def pair_to_api(pair: str) -> str:
    base, quote = pair.upper().split("/")
    if base == "BTC":
        base = "XBT"
    return f"{base}{quote}"


def to_ts(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()


def from_ts(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def fetch_ohlc(api_pair: str, interval: int, since: float | None) -> tuple[list, float | None]:
    """Fetch one batch. Returns (rows, last_ts)."""
    params = {"pair": api_pair, "interval": interval}
    if since:
        params["since"] = since

    resp = requests.get(KRAKEN_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise ValueError(f"Kraken API error: {data['error']}")

    result = data.get("result", {})
    # Kraken may return the data under a different key name
    rows = None
    for key in result:
        if key != "last":
            rows = result[key]
            break

    if not rows:
        return [], None

    last_ts = result.get("last")
    return rows, last_ts


def download_pair(pair: str, interval: int, since_ts: float | None, until_ts: float | None) -> list[dict]:
    """Download all OHLCV for one pair with pagination."""
    api_pair = pair_to_api(pair)
    all_rows = []
    cursor = since_ts

    while True:
        rows, last_ts = fetch_ohlc(api_pair, interval, cursor)
        if not rows:
            break

        for row in rows:
            ts = int(row[0])
            if until_ts and ts > until_ts:
                continue
            all_rows.append({
                "timestamp": from_ts(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "vwap": float(row[5]),
                "volume": float(row[6]),
                "count": int(row[7]),
            })

        print(f"  {pair}: {len(all_rows)} bars so far")
        cursor = last_ts

        if last_ts is None or (until_ts and last_ts >= until_ts):
            break

        sleep(REQUEST_DELAY)

    return all_rows


def save_csv(rows: list[dict], path: Path):
    if not rows:
        print(f"  No data for {path.name}, skipping")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved {path}: {len(rows)} bars")


def main():
    parser = argparse.ArgumentParser(description="Download Kraken OHLCV for multiple pairs")
    parser.add_argument("--pair", type=str, help="Single pair (default: all liquid pairs)")
    parser.add_argument("--interval", type=int, default=240, choices=[*DEFAULT_INTERVALS], help="Interval in minutes")
    parser.add_argument("--since", type=str, default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", type=str, default="2026-05-01", help="End date")
    parser.add_argument("--output-dir", type=str, default="data/kraken_4h", help="Output directory")
    args = parser.parse_args()

    pairs = DEFAULT_PAIRS if args.pair is None else [args.pair]
    since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    until_ts = int(datetime.strptime(args.until, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    out = Path(args.output_dir)

    for pair in pairs:
        safe = pair.replace("/", "_")
        csv_path = out / f"{safe}_{args.interval}.csv"
        if csv_path.exists():
            print(f"Skip {pair} (already exists)")
            continue
        print(f"\nDownloading {pair} ({args.interval}m) ...")
        try:
            rows = download_pair(pair, args.interval, since_ts, until_ts)
            save_csv(rows, csv_path)
        except Exception as e:
            print(f"  ERROR for {pair}: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
