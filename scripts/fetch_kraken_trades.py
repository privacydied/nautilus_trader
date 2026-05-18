#!/usr/bin/env python3
"""Fetch Kraken public trade data for committed multi-date windows.

Reads the precommitment file (OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json)
to determine which dates, pairs, and UTC windows to fetch.

Usage:
  python3 scripts/fetch_kraken_trades.py [--out data/kraken_trades]

No auth required. Public Kraken Trades API only.
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

KRAKEN_URL = "https://api.kraken.com/0/public/Trades"
USER_AGENT = "kraken-trade-fetcher/1.0 (research)"
REQUEST_DELAY = 0.5  # seconds between requests
MAX_REQUESTS = 50

PRECOMMITMENT_REPO_RELATIVE = "OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json"


def resolve_precommitment() -> Path:
    """Walk up from script dir to find precommitment at repo root."""
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        p = parent / PRECOMMITMENT_REPO_RELATIVE
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Could not find {PRECOMMITMENT_REPO_RELATIVE} from {candidate}"
    )


def date_to_unix(date_str: str, time_str: str) -> int:
    return int(datetime.fromisoformat(f"{date_str}T{time_str}+00:00").timestamp())


def fetch_trades(pair: str, since_s: int) -> list[list]:
    """Fetch trades from Kraken API starting from since_s (Unix seconds)."""
    all_trades: list[list] = []
    last: int | str | None = since_s

    with httpx.Client(timeout=30.0) as client:
        for _ in range(MAX_REQUESTS):
            params: dict = {"pair": pair}
            if last is not None:
                params["since"] = last

            try:
                resp = client.get(
                    KRAKEN_URL, params=params,
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                body = resp.json()
            except Exception as e:
                print(f"  Request failed: {e}", file=sys.stderr)
                break

            if body.get("error"):
                print(f"  API error: {body['error']}", file=sys.stderr)
                break

            result = body.get("result", {})
            trades = None
            for k, v in result.items():
                if k != "last":
                    trades = v
                    break
            if not trades:
                break

            all_trades.extend(trades)

            new_last = result.get("last")
            if new_last is None:
                break
            if new_last == last or (isinstance(last, str) and new_last == last):
                break
            last = new_last

            time.sleep(REQUEST_DELAY)

    return all_trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Kraken trades for committed windows")
    parser.add_argument("--out", default="data/kraken_trades", help="Output directory for CSVs")
    args = parser.parse_args()

    # Resolve repo root and precommitment
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    os.chdir(repo_root)

    precommit_path = resolve_precommitment()
    precommit = json.loads(precommit_path.read_text(encoding="utf-8"))

    # Extract multi-date windows from precommitment
    multi_date_windows = precommit.get("multi_date_windows")
    if not multi_date_windows:
        print(
            "ERROR: precommitment has no multi_date_windows field.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Extract Kraken pair config from data_config.streams
    streams = precommit.get("data_config", {}).get("streams", [])
    kraken_pairs = []
    for s in streams:
        if s.get("source_kind") == "kraken_trades":
            kraken_pairs.append((s["pair"], s["symbol"]))
    if not kraken_pairs:
        print("ERROR: no kraken_trades streams in precommitment.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_trades = 0

    for entry in multi_date_windows:
        label = entry["label"]
        date_str = entry["date"]
        win_start = entry.get("window_start_utc", "13:00:00")
        win_end = entry.get("window_end_utc", "16:00:00")

        win_start_s = date_to_unix(date_str, win_start)
        win_end_s = date_to_unix(date_str, win_end)

        for pair, symbol in kraken_pairs:
            out_path = out_dir / f"{pair}__{label}.csv"
            if out_path.exists() and out_path.stat().st_size > 0:
                print(f"EXISTS {pair} {label} ({date_str}) — skipping")
                total_files += 1
                continue

            print(f"FETCH {pair} {label} ({date_str})...", flush=True)
            all_trades = fetch_trades(pair, win_start_s - 60)

            # Filter to exact window
            kept = [t for t in all_trades if win_start_s <= float(t[2]) <= win_end_s]

            with open(out_path, "w", newline="") as f:
                w = csv.writer(f)
                for t in kept:
                    w.writerow([float(t[2]), float(t[0]), float(t[1])])

            print(f"  \u2192 {len(kept)} trades \u2192 {out_path}", flush=True)
            total_files += 1
            total_trades += len(kept)

    print(f"\nDONE: {total_files} files, ~{total_trades} trades total.", flush=True)


if __name__ == "__main__":
    main()
