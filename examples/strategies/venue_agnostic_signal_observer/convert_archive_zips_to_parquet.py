#!/usr/bin/env python3
"""
Convert cached Binance Vision aggTrade zips to Parquet.

Reads zipped CSVs from the binance_vision cache, writes one Parquet file per
symbol-day. Never produces JSONL. Never makes network calls.

Safety mode: public_data_observer_only — no orders, no auth, no execution.

Columns retained (minimal schema the study actually reads):
    ts_event  : int64  — nanosecond epoch timestamp
    price     : float32 — trade price
    size      : float32 — trade quantity (absolute)
    is_buyer_maker : bool — True = sell (maker was buyer), False = buy

Dropped columns (large, poorly-compressing int64s the study never reads):
    agg_trade_id, first_trade_id, last_trade_id, is_best_match

Precision choice: float32 for price and size.
    Rationale: Binance spot prices for BTC/ETH/SOL/LINK/DOGE/AVAX fit in
    float32 without loss for bps-level return math. float32 has ~7.2 decimal
    digits of precision. For BTC at $100k that's ~0.01 USD resolution = 0.1 bps,
    well below any signal threshold. 50% smaller on disk vs float64.

Compression: ZSTD level 3 (good speed/compression tradeoff).
Row group size: 1,000,000 rows (sensible for daily tick files).

Resumable: if a valid Parquet already exists for a symbol-day, skip it.
"""

from __future__ import annotations

import io
import os
import sys
import time
import zipfile
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple


SAFETY_MODE = "public_data_observer_only"

# ── Paths ─────────────────────────────────────────────────────────────────────
CACHE_ROOT = Path(
    os.environ.get(
        "ARCHIVE_ZIP_CACHE",
        "/mnt/nasirjones/py/nautilus_trader/data/binance_vision/cross_asset_beta_lag_archive_v0",
    )
)
PARQUET_ROOT = CACHE_ROOT / "parquet"

# ── Parquet settings ─────────────────────────────────────────────────────────
COMPRESSION = "ZSTD"
COMPRESSION_LEVEL = 3
ROW_GROUP_SIZE = 1_000_000

# AggTrade CSV columns (Binance Vision, no header):
#   0: agg_trade_id   1: price   2: quantity   3: first_trade_id
#   4: last_trade_id  5: timestamp(ms)  6: is_buyer_maker  7: is_best_match
AGGTRADE_COL_INDICES = {
    "price": 1,
    "quantity": 2,
    "timestamp": 5,
    "is_buyer_maker": 6,
}

HEARTBEAT_INTERVAL_FILES = 50
HEARTBEAT_INTERVAL_SECONDS = 120  # fallback: print every 2 min


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ts_to_ns(ts_value: int) -> int:
    """
    Auto-detect timestamp unit and convert to nanoseconds.

    Binance aggTrade timestamps changed format in 2025:
    - Pre-2025: milliseconds (13 digits)
    - 2025+: microseconds (16 digits)
    """
    s = str(abs(ts_value))
    if len(s) >= 18:
        return ts_value  # already ns
    elif len(s) >= 15:
        return ts_value * 1_000  # us -> ns
    else:
        return ts_value * 1_000_000  # ms -> ns


def _parse_zip_csv(csv_bytes: bytes) -> Tuple[list, list, list, list]:
    """
    Parse a Binance Vision aggTrade CSV (no header) into column arrays.

    Returns (ts_event_ns, price_f32, size_f32, is_buyer_maker_bool).
    Uses csv.reader for correct CSV field splitting.
    """
    import csv

    ts_ns_list: list = []
    price_list: list = []
    size_list: list = []
    side_list: list = []

    text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 7:
            continue
        try:
            ts_raw = int(row[AGGTRADE_COL_INDICES["timestamp"]])
            price = float(row[AGGTRADE_COL_INDICES["price"]])
            size = abs(float(row[AGGTRADE_COL_INDICES["quantity"]]))
            is_buyer_maker_raw = row[AGGTRADE_COL_INDICES["is_buyer_maker"]].strip().lower()
        except (ValueError, IndexError):
            continue

        if not (price > 0) or not (size > 0):
            continue

        ts_ns_list.append(_ts_to_ns(ts_raw))
        price_list.append(price)
        size_list.append(size)
        side_list.append(is_buyer_maker_raw in ("true", "1"))

    return ts_ns_list, price_list, size_list, side_list


def _zip_to_parquet(zf: zipfile.ZipFile, csv_name: str) -> pa.Table:  # type: ignore[name-defined]  # noqa: F821
    """Read one CSV from a zip and return a pyarrow Table."""
    import pyarrow as pa

    with zf.open(csv_name) as f:
        csv_bytes = f.read()

    ts_ns, prices, sizes, sides = _parse_zip_csv(csv_bytes)

    if not ts_ns:
        return pa.table({
            "ts_event": pa.array([], type=pa.int64()),
            "price": pa.array([], type=pa.float32()),
            "size": pa.array([], type=pa.float32()),
            "is_buyer_maker": pa.array([], type=pa.bool_()),
        })

    table = pa.table({
        "ts_event": pa.array(ts_ns, type=pa.int64()),
        "price": pa.array(prices, type=pa.float32()),
        "size": pa.array(sizes, type=pa.float32()),
        "is_buyer_maker": pa.array(sides, type=pa.bool_()),
    })
    return table


def _is_valid_parquet(path: Path) -> bool:
    """Check if a Parquet file exists and has the expected schema with nonzero rows."""
    import pyarrow.parquet as pq

    if not path.exists():
        return False
    if path.stat().st_size == 0:
        return False
    try:
        schema = pq.read_schema(path)
        expected_fields = {"ts_event", "price", "size", "is_buyer_maker"}
        if set(schema.names) != expected_fields:
            return False
        meta = pq.read_metadata(path)
        return meta.num_rows > 0
    except Exception:
        return False


def _discover_zip_files() -> List[Tuple[str, str, Path]]:
    """
    Walk the cache root and find all aggTrade zip files.

    Returns list of (symbol_upper, date_str, zip_path).
    Filename pattern: {SYMBOL}_{YYYY-MM-DD}_aggTrades.zip
    """
    results: List[Tuple[str, str, Path]] = []
    for sym_dir in sorted(CACHE_ROOT.iterdir()):
        if not sym_dir.is_dir():
            continue
        sym_name = sym_dir.name.upper()
        for zf in sorted(sym_dir.glob("*_aggTrades.zip")):
            # Extract date from filename
            # e.g. BTCUSDT_2024-01-01_aggTrades.zip
            base = zf.stem  # BTCUSDT_2024-01-01_aggTrades
            # Remove trailing _aggTrades
            if not base.endswith("_aggTrades"):
                continue
            base = base[: -len("_aggTrades")]  # BTCUSDT_2024-01-01
            # Split on last underscore to get date
            parts = base.rsplit("_", 1)
            if len(parts) != 2:
                continue
            date_str = parts[1]  # 2024-01-01
            results.append((sym_name, date_str, zf))
    return results


def convert_all(
    *,
    force: bool = False,
    heartbeat_interval_files: int = HEARTBEAT_INTERVAL_FILES,
    heartbeat_interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
) -> Dict[str, Any]:
    """
    Convert all cached aggTrade zips to Parquet.

    Parameters
    ----------
    force : bool
        If True, overwrite existing Parquet files.
    heartbeat_interval_files : int
        Print a heartbeat line every N files processed.
    heartbeat_interval_seconds : int
        Also print a heartbeat every N seconds.

    Returns
    -------
    dict with summary stats.
    """
    import pyarrow.parquet as pq

    # Discover zip files
    print(f"[{_now_utc_iso()}] PHASE 1: Discovering cached zip files...")
    zip_files = _discover_zip_files()
    print(f"  Found {len(zip_files)} zip files across {len({s for s, _, _ in zip_files})} symbols")

    if not zip_files:
        print("  No zip files found. Nothing to do.")
        return {"processed": 0, "skipped": 0, "errors": 0, "total_rows": 0}

    PARQUET_ROOT.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0
    errors = 0
    total_rows = 0
    start_time = time.monotonic()
    last_heartbeat = start_time

    print(f"\n[{_now_utc_iso()}] PHASE 2: Converting zips to Parquet...")
    print(f"  Output: {PARQUET_ROOT}")
    print(f"  Compression: {COMPRESSION} level {COMPRESSION_LEVEL}")
    print(f"  Row group size: {ROW_GROUP_SIZE}")
    print("  Price/size precision: float32")
    print(f"  Force overwrite: {force}")
    print()

    for idx, (sym, date_str, zip_path) in enumerate(zip_files):
        # Determine output path (mirror subdir structure)
        sym_lower = sym.lower()
        out_dir = PARQUET_ROOT / sym_lower
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{sym_lower}_aggTrades_{date_str}.parquet"

        # Resumable: skip if valid Parquet already exists
        if not force and _is_valid_parquet(out_path):
            skipped += 1
            continue

        try:
            with zipfile.ZipFile(zip_path) as zf:
                csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_names:
                    print(f"  SKIP {sym} {date_str}: no CSV in zip")
                    skipped += 1
                    continue

                table = _zip_to_parquet(zf, csv_names[0])

            if table.num_rows == 0:
                print(f"  SKIP {sym} {date_str}: 0 valid rows")
                skipped += 1
                continue

            # Write Parquet
            pq.write_table(
                table,
                out_path,
                compression=COMPRESSION,
                compression_level=COMPRESSION_LEVEL,
                row_group_size=ROW_GROUP_SIZE,
                use_dictionary=False,  # bool_ dict is tiny; int64 ts_event not worth it
            )

            processed += 1
            total_rows += table.num_rows

            # Heartbeat
            now = time.monotonic()
            if (processed % heartbeat_interval_files == 0 or
                    now - last_heartbeat >= heartbeat_interval_seconds):
                elapsed = now - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                eta_sec = (len(zip_files) - idx - 1) / rate if rate > 0 else 0
                print(
                    f"  HEARTBEAT: {processed}/{len(zip_files)} done, "
                    f"{skipped} skipped, {errors} errors, "
                    f"{total_rows:,} rows, "
                    f"{rate:.1f} files/sec, ETA {eta_sec/60:.0f}m"
                )
                last_heartbeat = now

        except Exception as e:
            errors += 1
            print(f"  ERROR {sym} {date_str}: {e}", flush=True)

    elapsed = time.monotonic() - start_time

    # Summary
    print(f"\n[{_now_utc_iso()}] PHASE 3: Summary")
    print(f"  Processed: {processed} files")
    print(f"  Skipped (already exist): {skipped} files")
    print(f"  Errors: {errors} files")
    print(f"  Total rows written: {total_rows:,}")
    print(f"  Elapsed: {elapsed:.1f}s")

    # Report Parquet directory size
    if PARQUET_ROOT.exists():
        total_size = sum(f.stat().st_size for f in PARQUET_ROOT.rglob("*.parquet"))
        print(f"  Parquet output size: {total_size / (1024**3):.2f} GB")

    # Report zip input size
    total_zip_size = sum(zf.stat().st_size for _, _, zf in zip_files)
    print(f"  Zip input size: {total_zip_size / (1024**3):.2f} GB")

    if total_zip_size > 0 and processed > 0:
        ratio = total_zip_size / total_size if total_size > 0 else float("inf")
        print(f"  Compression ratio (zip:parquet): {ratio:.2f}x")

    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_rows": total_rows,
        "elapsed_seconds": elapsed,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Convert cached aggTrade zips to Parquet")
    parser.add_argument("--force", action="store_true", help="Overwrite existing Parquet files")
    parser.add_argument("--cache-root", type=str, default=str(CACHE_ROOT),
                        help="Override cache root directory")
    parser.add_argument("--heartbeat-every", type=int, default=HEARTBEAT_INTERVAL_FILES,
                        help="Print heartbeat every N files (default: 50)")
    args = parser.parse_args()

    if args.cache_root:
        CACHE_ROOT = Path(args.cache_root)
        PARQUET_ROOT = CACHE_ROOT / "parquet"

    # Unbuffered stdout
    sys.stdout.reconfigure(line_buffering=True)

    print("=== Zip-to-Parquet Converter ===", flush=True)
    print(f"SAFETY_MODE: {SAFETY_MODE}", flush=True)
    print(f"Cache root: {CACHE_ROOT}", flush=True)
    print(f"Parquet output: {PARQUET_ROOT}", flush=True)
    print("==============================\n", flush=True)

    result = convert_all(force=args.force, heartbeat_interval_files=args.heartbeat_every)
    print(f"\nDone. Result: {result}", flush=True)
