"""Build 1h OHLCV bars from Hyperliquid S3 trade JSONL files.

The official Hyperliquid S3 archive contains trade records in JSONL format, e.g.::

    {"coin":"BTC","px":"84200.0","sz":"0.01","time":"2025-03-22T10:00:00.123456789"}

Historically the parser accessed fields via ``t.get(b'coin')`` assuming ``orjson.loads``
returned ``bytes`` keys. Modern ``orjson`` returns normal ``str`` keys, causing the
lookup to fail and resulting in zero retained trades.  This script uses the
standard ``json`` module (or ``orjson`` if available) and accesses keys with
string literals, matching the corrected behaviour.

The script is deliberately minimal – it validates input arguments, parses all
JSONL files under ``--input-root`` (recursively), filters to the requested
``--symbol`` list, aggregates to 1‑hour bars, and writes CSV/Parquet outputs.
It does **not** perform any network calls, live trading, or model training.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

import pandas as pd

# Optional ultra‑fast parser – fall back to stdlib if unavailable
try:
    import orjson  # type: ignore
except Exception:  # pragma: no cover
    orjson = None

# Optional LZ4 decompression for S3 compressed archives
try:
    import lz4.frame  # type: ignore
except Exception:  # pragma: no cover
    lz4 = None


def _load_jsonl(path: Path) -> List[dict]:
    """Load a JSONL file.

    Returns a list of parsed dictionaries.  Uses ``orjson`` when available for
    speed, but always accesses fields via **string** keys.
    
    Supports both plain .jsonl files and .jsonl.lz4 or .lz4 compressed files.
    """
    records: List[dict] = []
    
    # Determine if file is lz4 compressed
    is_lz4 = path.suffix == ".lz4" or path.name.endswith(".jsonl.lz4")
    
    if is_lz4:
        if lz4 is None:
            raise RuntimeError(f"LZ4 compression detected but lz4 package not available: {path}")
        with lz4.frame.open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if orjson is not None:
                        # ``orjson`` works with ``bytes``; it returns ``dict`` with str keys.
                        obj = orjson.loads(line)
                    else:
                        # line is already str from lz4.frame.open iteration
                        obj = json.loads(line)
                except Exception:
                    # Silently skip malformed lines – they are rare and non‑critical.
                    continue
                records.append(obj)
    else:
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if orjson is not None:
                        # ``orjson`` works with ``bytes``; it returns ``dict`` with str keys.
                        obj = orjson.loads(line)
                    else:
                        obj = json.loads(line.decode("utf-8"))
                except Exception:
                    # Silently skip malformed lines – they are rare and non‑critical.
                    continue
                records.append(obj)
    return records


def _parse_trade(record: dict) -> tuple[datetime, str, float, float] | None:
    """Extract timestamp, symbol, price, size from a raw trade dict.

    Returns ``None`` if required fields are missing or invalid.
    """
    try:
        coin = record.get("coin", "")
        if coin not in {"BTC", "ETH"}:
            return None
        # ``time`` is ISO‑8601 with optional nanosecond fraction.
        ts_raw = record.get("time")
        if not ts_raw:
            return None
        # ``datetime.fromisoformat`` handles up to nanosecond precision.
        ts = datetime.fromisoformat(ts_raw)
        # Ensure UTC – Hyperliquid timestamps are UTC.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        price = float(record["px"])
        size = float(record["sz"])
        return ts, coin, price, size
    except Exception:
        return None


def _aggregate_trades(trades: List[tuple[datetime, str, float, float]]) -> pd.DataFrame:
    """Convert a list of parsed trades into 1‑hour OHLCV bars.

    The resulting DataFrame has columns ``timestamp, symbol, open, high, low, close,
    volume`` and ``timestamp`` is the start of the hour (UTC).
    """
    if not trades:
        return pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(trades, columns=["ts", "symbol", "price", "size"])
    df["timestamp"] = df["ts"].dt.floor("h")
    # Group by hour and symbol
    agg = (
        df.groupby(["timestamp", "symbol"])
        .agg(open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("size", "sum"))
        .reset_index()
    )
    # Order columns as required
    agg = agg[["timestamp", "symbol", "open", "high", "low", "close", "volume"]]
    # Ensure correct dtypes
    agg["open"] = agg["open"].astype(float)
    agg["high"] = agg["high"].astype(float)
    agg["low"] = agg["low"].astype(float)
    agg["close"] = agg["close"].astype(float)
    agg["volume"] = agg["volume"].astype(float)
    return agg


def _collect_trades(input_root: Path, symbols: Iterable[str]) -> List[tuple[datetime, str, float, float]]:
    """Recursively read all JSONL files under ``input_root`` and return parsed trades.
    Only records for the requested ``symbols`` are kept.
    
    Supports both .jsonl and .lz4 (or .jsonl.lz4) compressed files.
    """
    import sys
    
    symbol_set = set(symbols)
    collected: List[tuple[datetime, str, float, float]] = []
    # Match both .jsonl and .lz4 files
    files_processed = 0
    for pattern in ["*.jsonl", "*.jsonl.lz4", "*.lz4"]:
        for path in input_root.rglob(pattern):
            files_processed += 1
            if files_processed % 100 == 0:
                print(f"Processed {files_processed} files, collected {len(collected)} trades...", file=sys.stderr, flush=True)
            for rec in _load_jsonl(path):
                # Quick filter before full parse
                if rec.get("coin") not in symbol_set:
                    continue
                parsed = _parse_trade(rec)
                if parsed:
                    collected.append(parsed)
    print(f"Final: {files_processed} files, {len(collected)} trades", file=sys.stderr, flush=True)
    return collected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build 1h OHLCV bars from Hyperliquid S3 trade JSONL files.")
    parser.add_argument("--input-root", type=Path, required=True,
                        help="Root directory containing raw S3 trade *.jsonl files.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory where CSV/Parquet outputs will be written.")
    parser.add_argument("--symbol", action="append", required=True,
                        help="Symbol to include (e.g. BTC). Can be repeated.")
    parser.add_argument("--write-csv", action="store_true",
                        help="Write CSV output (default: write parquet).")
    parser.add_argument("--write-parquet", action="store_true",
                        help="Write Parquet output.")
    args = parser.parse_args()

    if not args.write_csv and not args.write_parquet:
        # Default to parquet if nothing specified
        args.write_parquet = True

    args.output_dir.mkdir(parents=True, exist_ok=True)

    trades = _collect_trades(args.input_root, args.symbol)
    if not trades:
        print("No trades found for the requested symbols.", file=sys.stderr)
        sys.exit(1)

    bars = _aggregate_trades(trades)

    # Write outputs
    base_name = "hyperliquid_btc_eth_1h_bars"
    if args.write_csv:
        csv_path = args.output_dir / f"{base_name}.csv"
        bars.to_csv(csv_path, index=False)
        print(f"Wrote CSV: {csv_path}")
    if args.write_parquet:
        parquet_path = args.output_dir / f"{base_name}.parquet"
        bars.to_parquet(parquet_path, index=False)
        print(f"Wrote Parquet: {parquet_path}")

    # Also write a simple summary.json for downstream diagnostics
    summary = {
        "status": "OK",
        "rows": len(bars),
        "symbols": sorted(list(set(bars["symbol"]))),
        "start": bars["timestamp"].min().isoformat() if not bars.empty else None,
        "end": bars["timestamp"].max().isoformat() if not bars.empty else None,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("Summary written to summary.json")


if __name__ == "__main__":
    main()