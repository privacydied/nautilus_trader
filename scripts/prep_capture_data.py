"""Convert Hermes capture JSONL trade files to CSV format for Phase 1 parsers.

Converts the unified JSONL capture format (ts_event, venue, symbol, price, size,
side, trade_id) into the venue-specific CSV formats expected by each Phase 1
offline historical parser.

Supported output formats:
- binance_um_agg_trades: agg_trade_id,price,qty,...,transact_time(ms),is_buyer_maker
- kraken_trades: timestamp(s),price,volume
- coinbase_trades: time(s),price,size,side
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONVERTER_VERSION = "capture_csv_converter_v1"


def _get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_venue(venue_raw: str) -> str:
    """Normalize venue string from capture to the offline pipeline venue name."""
    mapping = {
        "binance_perp": "binance_perp",
        "binance": "binance_perp",
        "kraken": "kraken",
        "coinbase": "coinbase",
    }
    return mapping.get(venue_raw, venue_raw)


def _to_source_kind(venue_raw: str) -> str:
    """Map capture venue to appropriate offline source kind."""
    mapping = {
        "binance_perp": "binance_um_agg_trades",
        "binance": "binance_um_agg_trades",
        "kraken": "kraken_trades",
        "coinbase": "coinbase_trades",
    }
    return mapping.get(venue_raw, venue_raw)


def _resolve_symbol(venue_raw: str, symbol: str) -> tuple[str, str, str]:
    """Resolve symbol, base_asset, quote_asset from capture symbol string."""
    # Symbol is already in format like BTC/USDT or BTC/USD
    # Keep as-is, just split into base/quote
    parts = symbol.replace("-", "/").split("/")
    if len(parts) == 2:
        return symbol.replace("-", "/"), parts[0], parts[1]
    return symbol, symbol, "USD"


def _side_to_is_buyer_maker(side: str | None) -> str:
    """Convert side to binance is_buyer_maker column value.

    In binance agg trade format: is_buyer_maker=true means the buyer is
    the maker, which is equivalent to a sell (market sell). We map:
    sell -> true (maker), buy -> false (taker).
    """
    if side and side.lower() == "buy":
        return "false"
    return "true"


def _write_binance_csv(rows: list[dict], csv_path: Path, venue: str) -> int:
    """Write CSV for binance_um_agg_trades parser.

    Expected columns: agg_trade_id,price,qty,first_trade_id,last_trade_id,transact_time,is_buyer_maker
    transact_time in milliseconds.
    """
    with open(csv_path, "w") as f:
        f.write("agg_trade_id,price,qty,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n")
        written = 0
        for row in sorted(rows, key=lambda r: r["ts_event"]):
            trade_id = str(row.get("trade_id", ""))
            price = str(row["price"])
            qty = str(row.get("size", row.get("qty", "")))
            ts_ms = str(int(row["ts_event"] // 1_000_000))  # ns to ms
            is_maker = _side_to_is_buyer_maker(row.get("side"))
            f.write(f"{trade_id},{price},{qty},,,{ts_ms},{is_maker}\n")
            written += 1
    return written


def _write_kraken_csv(rows: list[dict], csv_path: Path) -> int:
    """Write CSV for kraken_trades parser.

    Expected columns: timestamp(s), price, volume
    """
    with open(csv_path, "w") as f:
        written = 0
        for row in sorted(rows, key=lambda r: r["ts_event"]):
            ts_s = str(row["ts_event"] / 1_000_000_000)
            price = str(row["price"])
            volume = str(row.get("size", row.get("qty", "")))
            f.write(f"{ts_s},{price},{volume}\n")
            written += 1
    return written


def _write_coinbase_csv(rows: list[dict], csv_path: Path) -> int:
    """Write CSV for coinbase_trades parser.

    Expected columns: time(s), price, size, side
    """
    with open(csv_path, "w") as f:
        written = 0
        for row in sorted(rows, key=lambda r: r["ts_event"]):
            ts_s = str(row["ts_event"] / 1_000_000_000)
            price = str(row["price"])
            size = str(row.get("size", row.get("qty", "")))
            side = str(row.get("side", "")).lower() if row.get("side") else ""
            f.write(f"{ts_s},{price},{size},{side}\n")
            written += 1
    return written


def _fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def convert_capture(
    input_root: Path,
    output_root: Path,
    *,
    allow_empty_targets: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert all JSONL capture files in input_root to venue-specific CSVs.

    Returns a conversion manifest dict.
    """
    if not input_root.is_dir():
        _fail(f"Input root does not exist: {input_root}")

    output_root = output_root.resolve()
    if output_root.exists():
        if not overwrite:
            _fail(
                f"Output root exists: {output_root}. "
                "Use --overwrite to allow writing to an existing directory."
            )
    else:
        output_root.mkdir(parents=True, exist_ok=True)

    # Discover JSONL trade files
    jsonl_paths = sorted(input_root.glob("trades_*.jsonl"))
    if not jsonl_paths:
        _fail(f"No trades_*.jsonl files found in {input_root}")

    per_file: list[dict[str, Any]] = []
    total_input_rows = 0
    total_output_rows = 0
    per_venue: dict[str, int] = {}
    per_symbol: dict[str, int] = {}
    all_timestamps: list[int] = []
    found_binance = False
    binance_output_ok = False

    for jsonl_path in jsonl_paths:
        # Parse filename for venue/symbol
        stem = jsonl_path.stem  # e.g. trades_binance_perp_BTC-USDT_stress_beta_...
        # Remove 'trades_' prefix
        after_prefix = stem[len("trades_"):]
        # Split on first occurrence of symbol pattern
        # The format is: trades_{venue}_{symbol}_stress_beta_...
        parts = after_prefix.split("_")
        # Find venue boundary: binance_perp has underscore, kraken/coinbase don't
        if parts[0] == "binance" and len(parts) > 1:
            venue_raw = "binance_perp"
            symbol_from_name = parts[2]  # BTC-USDT etc
        else:
            venue_raw = parts[0]
            symbol_from_name = parts[1]  # BTC-USD etc

        venue = _to_venue(venue_raw)
        source_kind = _to_source_kind(venue_raw)
        resolved_symbol, base_asset, quote_asset = _resolve_symbol(venue_raw, symbol_from_name)

        # Read JSONL rows
        raw_rows: list[dict] = []
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        raw_rows.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        _fail(f"Invalid JSON in {jsonl_path}: {e}")

        input_count = len(raw_rows)
        total_input_rows += input_count

        # Validate required fields
        required_fields = {"ts_event", "price"}
        for row in raw_rows:
            missing = required_fields - set(row.keys())
            if missing:
                _fail(
                    f"Row in {jsonl_path} missing required fields: {missing}. "
                    f"Row keys: {list(row.keys())}"
                )

        # Convert
        csv_filename = f"{venue}_{resolved_symbol.replace('/', '_')}.csv"
        csv_path = output_root / csv_filename

        if venue == "binance_perp" or source_kind == "binance_um_agg_trades":
            found_binance = True
            output_count = _write_binance_csv(raw_rows, csv_path, venue)
            if input_count > 0 and output_count > 0:
                binance_output_ok = True
        elif source_kind == "kraken_trades":
            output_count = _write_kraken_csv(raw_rows, csv_path)
        elif source_kind == "coinbase_trades":
            output_count = _write_coinbase_csv(raw_rows, csv_path)
        else:
            _fail(f"Unsupported venue/source_kind: {venue}/{source_kind} from {jsonl_path}")

        total_output_rows += output_count

        # Zero-output check
        if input_count > 0 and output_count == 0 and not allow_empty_targets:
            _fail(
                f"ZERO_OUTPUT: {jsonl_path.name} has {input_count} rows but "
                f"conversion produced 0 rows for {venue} {resolved_symbol}. "
                "Use --allow-empty-targets to override."
            )

        skipped = input_count - output_count
        timestamps = [row["ts_event"] for row in raw_rows]
        all_timestamps.extend(timestamps)
        first_ts = min(timestamps) if timestamps else None
        last_ts = max(timestamps) if timestamps else None

        per_venue[venue] = per_venue.get(venue, 0) + output_count
        per_symbol[resolved_symbol] = per_symbol.get(resolved_symbol, 0) + output_count

        per_file.append({
            "input_path": str(jsonl_path.resolve()),
            "output_path": str(csv_path.resolve()),
            "venue": venue,
            "symbol": resolved_symbol,
            "source_kind": source_kind,
            "input_row_count": input_count,
            "output_row_count": output_count,
            "skipped_row_count": skipped,
            "first_timestamp_ns": first_ts,
            "last_timestamp_ns": last_ts,
            "status": "OK" if output_count > 0 else "ZERO_OUTPUT",
        })

    # Required source check
    if not found_binance and not allow_empty_targets:
        _fail("No Binance source files found. Binance source streams are required.")
    if found_binance and not binance_output_ok and not allow_empty_targets:
        _fail(
            "Binance source files found but conversion produced 0 rows. "
            "Binance source streams are required to produce nonzero rows."
        )

    # Overall timestamps
    first_overall = min(all_timestamps) if all_timestamps else None
    last_overall = max(all_timestamps) if all_timestamps else None

    # Determine status
    if total_output_rows == 0:
        status = "CAPTURE_CONVERSION_ZERO_OUTPUT"
    elif not found_binance:
        status = "CAPTURE_CONVERSION_MISSING_REQUIRED_SOURCE"
    elif not binance_output_ok:
        status = "CAPTURE_CONVERSION_MISSING_REQUIRED_SOURCE"
    else:
        status = "CAPTURE_CONVERSION_READY"

    manifest = {
        "run_id": f"capture_conversion_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        "generated_at_utc": _now_utc_iso(),
        "git_sha": _get_git_sha(),
        "converter_version": CONVERTER_VERSION,
        "input_root": str(input_root.resolve()),
        "output_root": str(output_root),
        "per_file": per_file,
        "total_input_rows": total_input_rows,
        "total_output_rows": total_output_rows,
        "per_venue": per_venue,
        "per_symbol": per_symbol,
        "first_timestamp_ns": first_overall,
        "last_timestamp_ns": last_overall,
        "status": status,
        "safety": "public_data_observer_only",
    }

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Hermes capture JSONL trade files to parser-compatible CSVs."
    )
    parser.add_argument(
        "--input-root",
        required=True,
        help="Path to capture data directory containing trades_*.jsonl files",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Output directory for converted CSV files",
    )
    parser.add_argument(
        "--manifest-out",
        default=None,
        help="Path to write conversion manifest JSON (defaults to output-root/capture_conversion_manifest.json)",
    )
    parser.add_argument(
        "--allow-empty-targets",
        action="store_true",
        default=False,
        help="Allow target venue (kraken/coinbase) streams to produce zero converted rows",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting existing output directory",
    )

    args = parser.parse_args()

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)

    manifest = convert_capture(
        input_root,
        output_root,
        allow_empty_targets=args.allow_empty_targets,
        overwrite=args.overwrite,
    )

    # Write manifest
    if args.manifest_out:
        manifest_path = Path(args.manifest_out)
    else:
        manifest_path = output_root / "capture_conversion_manifest.json"

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # Print summary
    print(json.dumps(manifest, indent=2, default=str))

    # Exit with error code if status is not ready
    if manifest["status"] != "CAPTURE_CONVERSION_READY":
        print(f"\nERROR: Conversion status: {manifest['status']}", file=sys.stderr)
        if manifest["status"] == "CAPTURE_CONVERSION_ZERO_OUTPUT":
            print("No rows were produced from any input file.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
