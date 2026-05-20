"""Parallel pre-cache all Binance Vision aggTrade files for full evaluation.

Downloads all daily aggTrade files for all 6 symbols across the full
precommitted calendar using parallel threads. After this completes,
the evaluation runner reads from cache (near-instant).
"""

import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from examples.strategies.venue_agnostic_signal_observer.binance_vision_archive import (
    download_daily_agg_trades,
    _iter_date_range,
    estimate_file_size_mb,
)

ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT"]
CAL_START = "2024-01-01"
CAL_END = "2026-04-30"
MAX_WORKERS = 8

dates = _iter_date_range(CAL_START, CAL_END)
total_files = len(ALL_SYMBOLS) * len(dates)
total_mb = sum(estimate_file_size_mb(sym) for sym in ALL_SYMBOLS) * len(dates)
total_gb = total_mb / 1024

print(f"Pre-caching {total_files} files (~{total_gb:.1f} GB) with {MAX_WORKERS} threads...")
print(f"Start: {datetime.now(timezone.utc).isoformat()}")

# Build work items
work_items = []
for sym in ALL_SYMBOLS:
    for d in dates:
        work_items.append((sym, d))

print(f"Total work items: {len(work_items)}")

completed = 0
failed = 0
total_bytes = 0

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    future_map = {}
    for sym, d in work_items:
        future = executor.submit(download_daily_agg_trades, sym, d, cache=True)
        future_map[future] = (sym, d)

    for future in as_completed(future_map):
        sym, d = future_map[future]
        try:
            data, sha = future.result()
            if data is not None:
                total_bytes += len(data)
                completed += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL: {sym} {d}: {e}")

        if completed % 500 == 0:
            mb = total_bytes / (1024 * 1024)
            print(f"  Progress: {completed}/{total_files} files, {mb:.0f} MB")

print(f"\nDone!")
print(f"  Completed: {completed}/{total_files}")
print(f"  Failed: {failed}")
print(f"  Total bytes: {total_bytes} ({total_bytes/1024/1024/1024:.1f} GB)")
print(f"  End: {datetime.now(timezone.utc).isoformat()}")
