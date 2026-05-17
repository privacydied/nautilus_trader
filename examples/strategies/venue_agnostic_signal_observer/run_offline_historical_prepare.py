"""Phase 1 offline historical data-lane prepare runner.

Usage:
  uv run --no-sync python -m examples.strategies.venue_agnostic_signal_observer.run_offline_historical_prepare \\
    --source-config path/to/offline_sources.json \\
    --out reports/venue_agnostic_signal_observer/offline_historical_prepare \\
    [--precommitment PRECOMMITMENT.json] \\
    [--force-rehash] \\
    [--overwrite]

This runner is observer-only. It does NOT:
- submit orders
- use private keys or API secrets
- call exchange account APIs
- invoke live trading adapters
- implement hypothesis evaluation
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .offline_corpus_hash import HashCache, compute_data_corpus_hash
from .offline_historical_models import (
    OFFLINE_DATA_SCHEMA_VERSION,
    OfflineSourceFile,
    OfflineTradeRecord,
    OfflineBarRecord,
)
from .offline_historical_normalize import (
    to_nanoseconds,
)
from .offline_historical_sources import (
    assert_known_source_kind,
    parse_binance_agg_trades,
    parse_binance_klines,
    parse_coinbase_candles,
    parse_coinbase_trades,
    parse_kraken_ohlcvt,
    parse_kraken_trades,
    ParseResult,
)
from .offline_replay_manifest import (
    build_manifest,
    compute_precommitment_hash,
    write_manifest,
)


def _get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _parse_iso(ts: str) -> int:
    """Parse an ISO-8601 UTC string to nanoseconds."""
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1_000_000_000)


def _dispatch_parse(
    source_cfg: dict,
    hash_cache: HashCache,
    force_rehash: bool,
) -> tuple[OfflineSourceFile, ParseResult]:
    """Load and parse one source file entry from the source config."""
    venue = source_cfg["venue"]
    symbol = source_cfg["symbol"]
    base_asset = source_cfg["base_asset"]
    quote_asset = source_cfg["quote_asset"]
    source_kind = source_cfg["source_kind"]
    stream_type = source_cfg["stream_type"]
    resolution_type = source_cfg["resolution_type"]
    timestamp_unit = source_cfg["timestamp_unit"]
    path_str = source_cfg["path"]
    expected_start_ns = _parse_iso(source_cfg["expected_start"])
    expected_end_ns = _parse_iso(source_cfg["expected_end"])

    assert_known_source_kind(source_kind)

    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")

    stat = path.stat()
    sha, _ = hash_cache.get_or_compute(path, force_rehash=force_rehash)
    logical_source_id = source_cfg.get(
        "logical_source_id",
        f"{venue}__{symbol.replace('/', '_')}__{source_kind}",
    )

    # Parse
    if source_kind in ("binance_spot_agg_trades", "binance_um_agg_trades"):
        result = parse_binance_agg_trades(
            path=path,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            source_kind=source_kind,
            logical_source_id=logical_source_id,
            timestamp_unit=timestamp_unit,
            expected_start_ns=expected_start_ns,
            expected_end_ns=expected_end_ns,
        )
    elif source_kind in ("binance_spot_klines", "binance_um_klines"):
        result = parse_binance_klines(
            path=path,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            source_kind=source_kind,
            logical_source_id=logical_source_id,
            timestamp_unit=timestamp_unit,
            expected_start_ns=expected_start_ns,
            expected_end_ns=expected_end_ns,
        )
    elif source_kind == "kraken_ohlcvt":
        result = parse_kraken_ohlcvt(
            path=path,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            logical_source_id=logical_source_id,
            expected_start_ns=expected_start_ns,
            expected_end_ns=expected_end_ns,
        )
    elif source_kind == "kraken_trades":
        result = parse_kraken_trades(
            path=path,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            logical_source_id=logical_source_id,
            expected_start_ns=expected_start_ns,
            expected_end_ns=expected_end_ns,
        )
    elif source_kind == "coinbase_candles":
        result = parse_coinbase_candles(
            path=path,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            logical_source_id=logical_source_id,
            expected_start_ns=expected_start_ns,
            expected_end_ns=expected_end_ns,
        )
    elif source_kind == "coinbase_trades":
        result = parse_coinbase_trades(
            path=path,
            venue=venue,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            logical_source_id=logical_source_id,
            expected_start_ns=expected_start_ns,
            expected_end_ns=expected_end_ns,
        )
    else:
        raise ValueError(f"No parser implemented for source_kind={source_kind!r}")

    source_file = OfflineSourceFile(
        path=str(path.resolve()),
        logical_source_id=logical_source_id,
        venue=venue,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        source_kind=source_kind,
        stream_type=stream_type,
        resolution_type=resolution_type,
        timestamp_unit=timestamp_unit,
        expected_start_ns=expected_start_ns,
        expected_end_ns=expected_end_ns,
        file_size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        file_sha256=sha,
        row_count=result.row_count,
        data_start_ns=result.data_start_ns,
        data_end_ns=result.data_end_ns,
    )
    return source_file, result


def run(args: argparse.Namespace) -> int:
    source_cfg_path = Path(args.source_config)
    out_dir = Path(args.out)
    overwrite: bool = args.overwrite
    force_rehash: bool = args.force_rehash
    precommitment_path: Optional[Path] = (
        Path(args.precommitment) if args.precommitment else None
    )

    # Load source config
    source_config = json.loads(source_cfg_path.read_text(encoding="utf-8"))
    sources = source_config.get("sources", [])
    if not sources:
        print("ERROR: source config contains no sources.", file=sys.stderr)
        return 1

    hash_cache = HashCache()
    git_sha = _get_git_sha()

    source_files: List[OfflineSourceFile] = []
    trades_by_stream: Dict[str, List[OfflineTradeRecord]] = {}
    bars_by_stream: Dict[str, List[OfflineBarRecord]] = {}
    timestamp_validation_meta: dict = {"per_source": []}

    for i, src in enumerate(sources):
        try:
            sf, result = _dispatch_parse(src, hash_cache, force_rehash)
        except Exception as exc:
            print(f"ERROR parsing source[{i}] ({src.get('path')}): {exc}", file=sys.stderr)
            return 1

        source_files.append(sf)
        lsid = sf.logical_source_id
        if result.trades:
            trades_by_stream.setdefault(lsid, []).extend(result.trades)
        if result.bars:
            bars_by_stream.setdefault(lsid, []).extend(result.bars)
        timestamp_validation_meta["per_source"].append(
            {
                "logical_source_id": lsid,
                "row_count": result.row_count,
                "data_start_ns": result.data_start_ns,
                "data_end_ns": result.data_end_ns,
                "was_sorted": result.was_sorted,
            }
        )

    # Precommitment
    precommitment_hash: Optional[str] = None
    if precommitment_path is not None:
        if not precommitment_path.exists():
            print(f"ERROR: precommitment file not found: {precommitment_path}", file=sys.stderr)
            return 1
        precommitment_hash = compute_precommitment_hash(precommitment_path)

    # Build run_id
    run_id = "offline_prepare_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    manifest = build_manifest(
        run_id=run_id,
        git_sha=git_sha,
        source_files=source_files,
        trades_by_stream=trades_by_stream,
        bars_by_stream=bars_by_stream,
        hash_cache_used=True,
        hash_cache_entries_reused=hash_cache.entries_reused,
        hash_cache_entries_recomputed=hash_cache.entries_recomputed,
        precommitment_hash=precommitment_hash,
        timestamp_validation_meta=timestamp_validation_meta,
    )

    try:
        manifest_path = write_manifest(manifest, out_dir, overwrite=overwrite)
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Manifest written: {manifest_path}")
    print(f"  run_id             : {manifest.run_id}")
    print(f"  data_corpus_hash   : {manifest.data_corpus_hash}")
    print(f"  precommitment_hash : {manifest.precommitment_hash}")
    print(f"  source_files       : {len(source_files)}")
    print(f"  next_phase_allowed : {manifest.next_phase_allowed}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1 offline historical data-lane prepare runner."
    )
    parser.add_argument("--source-config", required=True, help="Path to offline_sources.json")
    parser.add_argument(
        "--out",
        default="reports/venue_agnostic_signal_observer/offline_historical_prepare",
        help="Output directory for run manifests",
    )
    parser.add_argument("--precommitment", default=None, help="Optional precommitment JSON file")
    parser.add_argument(
        "--force-rehash",
        action="store_true",
        default=False,
        help="Force recomputation of all file hashes, ignoring the cache",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting existing output directories",
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
