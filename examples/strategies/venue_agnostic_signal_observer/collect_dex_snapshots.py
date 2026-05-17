#!/usr/bin/env python3
"""Collect DEX pool snapshots over a time window and write JSONL.

**Observer-only. No execution, no keys, no orders, no live trading.**

Polls DEX Screener search for target asset symbols at a configurable
interval, parses results into DexPoolSnapshot, and appends them to a
JSONL file.

Usage:

    python -m examples.strategies.venue_agnostic_signal_observer.collect_dex_snapshots \
        --symbols SOL,LINK,AVAX,DOGE,ADA \
        --duration-seconds 1800 \
        --poll-interval-seconds 10 \
        --min-liquidity-usd 500000 \
        --min-volume-1h-usd 100000 \
        --out data/dex_cex_spot_dislocation_v1/dex_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from .dex_adapters import search_dexscreener_by_symbols, _RATE_LIMIT_DELAY
from .dex_models import DexPoolSnapshot


def collect_snapshots(
    symbols: list[str],
    duration_seconds: int,
    poll_interval_seconds: int,
    min_liquidity_usd: float,
    min_volume_1h_usd: float,
    out_path: str,
) -> tuple[int, list[str]]:
    """Run a timed collection loop polling DEX Screener.

    Returns (snapshot_count, warnings).
    Writes each valid snapshot to the JSONL file immediately.
    """
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    total = 0
    start = time.time()

    with httpx.Client(timeout=15) as client:
        while time.time() - start < duration_seconds:
            loop_start = time.time()
            snaps, errs = search_dexscreener_by_symbols(symbols, client=client)
            warnings.extend(errs)

            # Filter by liquidity/volume and deduplicate within this batch
            seen = set()
            for snap in snaps:
                if snap.liquidity_usd is not None and snap.liquidity_usd < min_liquidity_usd:
                    continue
                if snap.volume_1h_usd is not None and snap.volume_1h_usd < min_volume_1h_usd:
                    continue
                if snap.price_usd is None:
                    continue

                # Dedup: same chain+dex+pair_address in same batch
                key = (snap.chain, snap.dex, snap.pair_address)
                if key not in seen:
                    seen.add(key)
                    with open(p, "a") as f:
                        d = snap.to_dict()
                        d["collect_ts"] = int(loop_start)
                        f.write(json.dumps(d, default=str) + "\n")
                    total += 1

            elapsed = time.time() - loop_start
            sleep_needed = max(0, poll_interval_seconds - elapsed - _RATE_LIMIT_DELAY)
            if sleep_needed > 0:
                time.sleep(sleep_needed)

            # Progress log
            wall_elapsed = time.time() - start
            print(f"  [{wall_elapsed:.0f}s] batch complete: {len(snaps)} raw, "
                  f"{len(seen)} written, total={total}")

    return total, warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect DEX pool snapshots over a time window. Observer-only."
    )
    parser.add_argument(
        "--symbols", default="SOL,LINK,AVAX,DOGE,ADA",
        help="Comma-separated asset symbols to poll",
    )
    parser.add_argument(
        "--duration-seconds", type=int, default=1800,
        help="Collection duration in seconds (default: 1800 = 30 min)",
    )
    parser.add_argument(
        "--poll-interval-seconds", type=int, default=60,
        help="Seconds between search polls (default: 60)",
    )
    parser.add_argument(
        "--min-liquidity-usd", type=float, default=500_000,
        help="Minimum DEX pool liquidity to include",
    )
    parser.add_argument(
        "--min-volume-1h-usd", type=float, default=100_000,
        help="Minimum 1h volume to include",
    )
    parser.add_argument(
        "--out", required=True,
        help="Output JSONL file path",
    )
    args = parser.parse_args()

    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]

    print("=" * 60)
    print("DEX SNAPSHOT COLLECTOR  (observer-only)")
    print("=" * 60)
    print(f"  Symbols:  {', '.join(symbols)}")
    print(f"  Duration: {args.duration_seconds}s")
    print(f"  Interval: {args.poll_interval_seconds}s")
    print(f"  Min liq:  ${args.min_liquidity_usd:,.0f}")
    print(f"  Min vol1h: ${args.min_volume_1h_usd:,.0f}")
    print(f"  Output:   {args.out}")
    print()

    # Clear output file
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("")

    total, warnings = collect_snapshots(
        symbols,
        duration_seconds=args.duration_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        min_liquidity_usd=args.min_liquidity_usd,
        min_volume_1h_usd=args.min_volume_1h_usd,
        out_path=args.out,
    )

    print()
    print(f"Done. Wrote {total} snapshots to {args.out}")
    if warnings:
        print(f"Warnings: {len(warnings)}")
        for w in warnings[:5]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
