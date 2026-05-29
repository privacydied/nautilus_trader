#!/usr/bin/env python3
"""
Build 1h OHLCV bars from Hyperliquid node_fills_by_block LZ4 files.

Uses orjson for JSON parsing (string keys). Handles LZ4 compression.
Dedup by trade ID (tid) — each fill record is unique by tid.
Filters to BTC and ETH only (rejects LINK, @142, etc.).

Usage:
    uv run --no-sync python scripts/build_bars_from_hl_node_fills_2025_window.py \\
        --input-root .local_data/hyperliquid_s3_cache/node_fills_2025_window \\
        --output-dir reports/hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0/run_id \\
        --symbol BTC \\
        --symbol ETH \\
        --write-csv \\
        --write-parquet
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import lz4.frame  # noqa: F401  # always available
import orjson  # noqa: F401  # always available

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VALID_SYMBOLS = {"BTC", "ETH"}
OHLCV_COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]


def parse_args():
    parser = argparse.ArgumentParser(description="Build 1h OHLCV bars from HL node_fills_by_block")
    parser.add_argument("--input-root", required=True, help="Directory with downloaded LZ4 files")
    parser.add_argument("--output-dir", required=True, help="Output directory for bar files")
    parser.add_argument("--symbol", action="append", required=True, help="Symbol to include (repeatable)")
    parser.add_argument("--write-csv", action="store_true", help="Write CSV output")
    parser.add_argument("--write-parquet", action="store_true", help="Write Parquet output")
    return parser.parse_args()


def parse_lz4_file(filepath: Path) -> list[dict]:
    """Parse an LZ4 file, returning list of event dicts with string keys."""
    records = []
    raw = lz4.frame.open(str(filepath)).read()
    # The file contains concatenated JSON objects (no newlines between them)
    # We need to parse each one individually
    text = raw.decode("utf-8", errors="replace")

    # Use orjson for fast parsing if available, otherwise fallback
    if orjson is not None:
        try:
            # orjson.loads can parse concatenated JSON objects
            data = orjson.loads(text)
            if isinstance(data, dict) and "events" in data:
                # Single object with events array
                for evt in data.get("events", []):
                    if len(evt) >= 2:
                        addr, payload = evt[0], evt[1]
                        if orjson and isinstance(payload, bytes):
                            payload = orjson.loads(payload)
                        records.append({
                            "block_time": data.get("block_time", ""),
                            "time_ms": payload.get("time"),
                            "coin": payload.get("coin", ""),
                            "px": payload.get("px", "0"),
                            "sz": payload.get("sz", "0"),
                            "side": payload.get("side", ""),
                            "tid": payload.get("tid"),
                            "hash": payload.get("hash", ""),
                        })
            elif isinstance(data, list):
                # List of objects
                for obj in data:
                    if not isinstance(obj, dict):
                        continue
                    events = obj.get("events", [])
                    if not isinstance(events, list):
                        continue
                    for evt in events:
                        if len(evt) >= 2:
                            addr, payload = evt[0], evt[1]
                            if orjson and isinstance(payload, bytes):
                                payload = orjson.loads(payload)
                            records.append({
                                "block_time": obj.get("block_time", ""),
                                "time_ms": payload.get("time"),
                                "coin": payload.get("coin", ""),
                                "px": payload.get("px", "0"),
                                "sz": payload.get("sz", "0"),
                                "side": payload.get("side", ""),
                                "tid": payload.get("tid"),
                                "hash": payload.get("hash", ""),
                            })
        except (orjson.JSONDecodeError, json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to parse {filepath}: {e}")
    else:
        # Fallback: parse concatenated JSON objects
        # Each object starts with { and ends with }
        depth = 0
        start = 0
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj_str = text[start:i + 1]
                    try:
                        obj = json.loads(obj_str)
                    except json.JSONDecodeError:
                        continue
                    events = obj.get("events", [])
                    if not isinstance(events, list):
                        continue
                    for evt in events:
                        if len(evt) >= 2:
                            addr, payload = evt[0], evt[1]
                            if isinstance(payload, bytes):
                                try:
                                    payload = orjson.loads(payload)
                                except Exception:
                                    continue
                            records.append({
                                "block_time": obj.get("block_time", ""),
                                "time_ms": payload.get("time"),
                                "coin": payload.get("coin", ""),
                                "px": payload.get("px", "0"),
                                "sz": payload.get("sz", "0"),
                                "side": payload.get("side", ""),
                                "tid": payload.get("tid"),
                                "hash": payload.get("hash", ""),
                            })

    return records


def build_bars(records: list[dict], symbols: tuple[str, ...]) -> pd.DataFrame:
    """Build 1h OHLCV bars from raw fill records."""
    # Filter to valid symbols
    records = [r for r in records if r["coin"] in symbols and r["time_ms"] is not None]

    if not records:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    # Convert to DataFrame
    df = pd.DataFrame(records)
    df["time_ms"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    df["px"] = pd.to_numeric(df["px"], errors="coerce")
    df["sz"] = pd.to_numeric(df["sz"], errors="coerce")

    # Floor to hour boundary for bar grouping
    df["hour"] = df["time_ms"].dt.floor("h")

    # Group by symbol, hour
    bars = []
    for sym in sorted(symbols):
        sym_df = df[df["coin"] == sym]
        if len(sym_df) == 0:
            continue

        grouped = sym_df.groupby("hour")
        for hour, group in grouped:
            opens = group["px"][group["side"] == "B"]
            closes = group["px"][group["side"] == "A"]
            all_prices = group["px"].dropna()

            if len(all_prices) == 0:
                continue

            bars.append({
                "timestamp": hour,
                "symbol": sym,
                "open": float(all_prices.iloc[0]) if len(all_prices) > 0 else 0.0,
                "high": float(all_prices.max()),
                "low": float(all_prices.min()),
                "close": float(all_prices.iloc[-1]) if len(all_prices) > 0 else 0.0,
                "volume": float(group["sz"].sum()),
            })

    if not bars:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    result = pd.DataFrame(bars)
    result = result.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return result


def build_bars_v2(records: list[dict], symbols: tuple[str, ...]) -> pd.DataFrame:
    """Build 1h OHLCV bars from raw fill records using pandas for robustness."""
    # Filter to valid symbols
    records = [r for r in records if r["coin"] in symbols and r["time_ms"] is not None]

    if not records:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    # Convert to DataFrame
    df = pd.DataFrame(records)
    df["time_ms"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    df["px"] = pd.to_numeric(df["px"], errors="coerce")
    df["sz"] = pd.to_numeric(df["sz"], errors="coerce")

    # Floor to hour boundary for bar grouping
    df["hour"] = df["time_ms"].dt.floor("h")

    # Drop rows with NaN prices
    df = df.dropna(subset=["px"])

    if len(df) == 0:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    # Build bars per symbol per hour
    all_bars = []
    for sym in sorted(symbols):
        sym_df = df[df["coin"] == sym]
        if len(sym_df) == 0:
            continue

        for hour, group in sym_df.groupby("hour"):
            prices = group["px"].dropna()
            if len(prices) == 0:
                continue

            all_bars.append({
                "timestamp": hour,
                "symbol": sym,
                "open": float(prices.iloc[0]),
                "high": float(prices.max()),
                "low": float(prices.min()),
                "close": float(prices.iloc[-1]),
                "volume": float(group["sz"].sum()),
            })

    if not all_bars:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    result = pd.DataFrame(all_bars)
    result = result.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return result


def main():
    args = parse_args()
    symbols = tuple(args.symbol)

    input_root = Path(args.input_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all LZ4 files
    lz4_files = sorted(input_root.glob("**/*.lz4"))
    if not lz4_files:
        logger.error(f"No .lz4 files found in {input_root}")
        sys.exit(1)

    logger.info(f"Found {len(lz4_files)} LZ4 files")

    # Parse all files
    all_records = []
    for f in lz4_files:
        records = parse_lz4_file(f)
        all_records.extend(records)
        logger.info(f"Parsed {f.name}: {len(records)} records")

    logger.info(f"Total records parsed: {len(all_records)}")

    # Build bars
    bars = build_bars_v2(all_records, symbols)
    logger.info(f"Built {len(bars)} bars")

    if len(bars) == 0:
        logger.error("No bars built. Check symbol filter and data.")
        sys.exit(1)

    # Dedup check
    dedup_report = {
        "total_records": len(all_records),
        "total_bars": len(bars),
        "unique_symbol_timestamp": len(bars.drop_duplicates(subset=["symbol", "timestamp"])),
        "duplicate_symbol_timestamp": len(bars) - len(bars.drop_duplicates(subset=["symbol", "timestamp"])),
    }
    (output_dir / "dedup_report.json").write_text(
        json.dumps(dedup_report, indent=2, sort_keys=True) + "\n"
    )

    # Write parquet
    if args.write_parquet:
        bars.to_parquet(output_dir / "hyperliquid_btc_eth_1h_bars.parquet", index=False)
        logger.info(f"Wrote parquet: {output_dir / 'hyperliquid_btc_eth_1h_bars.parquet'}")

    # Write CSV
    if args.write_csv:
        bars.to_csv(output_dir / "hyperliquid_btc_eth_1h_bars.csv", index=False, float_format="%.8f")
        logger.info(f"Wrote CSV: {output_dir / 'hyperliquid_btc_eth_1h_bars.csv'}")

    # Summary
    summary = {
        "total_lz4_files": len(lz4_files),
        "total_raw_records": len(all_records),
        "total_bars": len(bars),
        "symbols": sorted(bars["symbol"].unique().tolist()),
        "per_symbol": {},
        "date_range": {
            "start": str(bars["timestamp"].min()),
            "end": str(bars["timestamp"].max()),
        },
        "source_prefix": str(input_root),
        "object_keys": [str(f.name) for f in lz4_files],
        "lz4_sha256": {},
    }
    for sym in sorted(bars["symbol"].unique()):
        sym_bars = bars[bars["symbol"] == sym]
        summary["per_symbol"][sym] = {
            "rows": len(sym_bars),
            "start": str(sym_bars["timestamp"].min()),
            "end": str(sym_bars["timestamp"].max()),
        }
    for f in lz4_files:
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        summary["lz4_sha256"][f.name] = h

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )

    # Summary MD
    md_lines = [
        "# Bar Building Summary",
        "",
        f"Total LZ4 files: {len(lz4_files)}",
        f"Total raw records: {len(all_records)}",
        f"Total bars: {len(bars)}",
        "",
        "## Per Symbol",
    ]
    for sym in sorted(bars["symbol"].unique()):
        sym_bars = bars[bars["symbol"] == sym]
        md_lines.extend([
            f"### {sym}",
            f"- Rows: {len(sym_bars)}",
            f"- Start: {sym_bars['timestamp'].min()}",
            f"- End: {sym_bars['timestamp'].max()}",
            "",
        ])
    md_lines.extend([
        f"## Date Range",
        f"- Start: {bars['timestamp'].min()}",
        f"- End: {bars['timestamp'].max()}",
        "",
        f"## Dedup",
        f"- Total records: {dedup_report['total_records']}",
        f"- Unique symbol/timestamp: {dedup_report['unique_symbol_timestamp']}",
        f"- Duplicates: {dedup_report['duplicate_symbol_timestamp']}",
    ])
    (output_dir / "summary.md").write_text("\n".join(md_lines) + "\n")

    # Gaps
    gaps = []
    for sym in sorted(bars["symbol"].unique()):
        sym_bars = bars[bars["symbol"] == sym].sort_values("timestamp")
        if len(sym_bars) < 2:
            continue
        ts = sym_bars["timestamp"].values
        diffs = np.diff(ts).astype("timedelta64[h]").astype(int)
        gap_mask = diffs > 1
        for idx in np.where(gap_mask)[0]:
            gap_start = pd.Timestamp(ts[idx]) + pd.Timedelta(hours=1)
            gap_end = pd.Timestamp(ts[idx + 1]) - pd.Timedelta(hours=1)
            gaps.append({
                "symbol": sym,
                "gap_start": str(gap_start),
                "gap_end": str(gap_end),
                "gap_hours": int(diffs[idx]) - 1,
            })

    (output_dir / "gaps.json").write_text(
        json.dumps({"gaps": gaps, "gap_count": len(gaps)}, indent=2, sort_keys=True) + "\n"
    )

    # Schema report
    schema_report = {
        "columns": list(bars.columns),
        "dtypes": {c: str(bars[c].dtype) for c in bars.columns},
        "sample_row": bars.iloc[0].to_dict() if len(bars) > 0 else {},
    }
    (output_dir / "schema_report.json").write_text(
        json.dumps(schema_report, indent=2, sort_keys=True, default=str) + "\n"
    )

    # Manifest
    manifest = {
        "source": "node_fills_by_block/hourly",
        "source_prefix": str(input_root),
        "total_lz4_files": len(lz4_files),
        "total_raw_records": len(all_records),
        "total_bars": len(bars),
        "symbols": sorted(bars["symbol"].unique().tolist()),
        "date_range": {
            "start": str(bars["timestamp"].min()),
            "end": str(bars["timestamp"].max()),
        },
        "per_symbol": summary["per_symbol"],
        "object_keys": [str(f.name) for f in lz4_files],
        "lz4_sha256": summary["lz4_sha256"],
        "dedup_report": dedup_report,
        "gap_count": len(gaps),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    print(f"Bars built: {len(bars)}")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()
