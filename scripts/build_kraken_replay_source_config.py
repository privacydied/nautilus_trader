#!/usr/bin/env python3
"""Build offline_sources.json from the committed precommitment multi_date_windows.

Derives source entries from OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json.
Each source entry references a local CSV file under data/kraken_trades/{pair}__{label}.csv.

Usage:
  python3 scripts/build_kraken_replay_source_config.py [--precommitment OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json] [--out OFFLINE_SOURCES.json]

The generated source config preserves the committed labels, dates, and UTC windows
from the precommitment's multi_date_windows field.
"""
import argparse
import json
from pathlib import Path


def resolve_from_repo_root(relative_path: str) -> Path:
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        p = parent / relative_path
        if p.exists():
            return p
    raise FileNotFoundError(f"Could not find {relative_path} from {candidate}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build offline_sources.json from committed precommitment"
    )
    parser.add_argument(
        "--precommitment",
        default="OFFLINE_HISTORICAL_REPLAY_PRECOMMITMENT.json",
        help="Path to precommitment JSON (default: repo-root relative)",
    )
    parser.add_argument(
        "--out",
        default="examples/strategies/venue_agnostic_signal_observer/offline_sources_multi_date.json",
        help="Output path for generated source config",
    )
    parser.add_argument(
        "--data-dir",
        default="data/kraken_trades",
        help="Directory containing fetched CSV files (relative to repo root)",
    )
    parser.add_argument(
        "--stream-type",
        default="trades",
        help="stream_type for source entries (default: trades)",
    )
    parser.add_argument(
        "--resolution-type",
        default="trade",
        help="resolution_type for source entries (default: trade)",
    )
    args = parser.parse_args()

    # Load precommitment
    precommit_path = Path(args.precommitment)
    if not precommit_path.exists():
        precommit_path = resolve_from_repo_root(args.precommitment)
    precommit = json.loads(precommit_path.read_text(encoding="utf-8"))

    # Extract multi_date_windows
    multi_date_windows = precommit.get("multi_date_windows")
    if not multi_date_windows:
        print("ERROR: precommitment has no multi_date_windows field.")
        raise SystemExit(1)

    # Extract Kraken pair config from data_config
    data_config = precommit.get("data_config", {})
    venue = data_config.get("venue", "kraken")
    streams = data_config.get("streams", [])
    kraken_streams = [s for s in streams if s.get("source_kind") == "kraken_trades"]
    if not kraken_streams:
        print("ERROR: no kraken_trades stream in precommitment data_config.streams")
        raise SystemExit(1)

    sources = []
    data_dir = args.data_dir.rstrip("/")

    for entry in multi_date_windows:
        label = entry["label"]
        date_str = entry["date"]
        win_start = entry.get("window_start_utc", "13:00:00")
        win_end = entry.get("window_end_utc", "16:00:00")
        start_iso = f"{date_str}T{win_start}"
        end_iso = f"{date_str}T{win_end}"

        for s in kraken_streams:
            pair = s["pair"]
            logical_source_id = s.get("logical_source_id") or (
                f"{venue}__{s['symbol'].replace('/', '_')}__{s['source_kind']}"
            )
            sources.append({
                "path": f"{data_dir}/{pair}__{label}.csv",
                "logical_source_id": logical_source_id,
                "venue": venue,
                "symbol": s["symbol"],
                "base_asset": s["base_asset"],
                "quote_asset": s["quote_asset"],
                "source_kind": s["source_kind"],
                "stream_type": args.stream_type,
                "resolution_type": args.resolution_type,
                "timestamp_unit": "s",
                "expected_start": start_iso,
                "expected_end": end_iso,
            })

    config = {"sources": sources, "generated_from_precommitment": str(precommit_path)}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Wrote {len(sources)} source entries to {out_path}")
    print(f"  Derived from precommitment: {precommit_path}")
    print(f"  Data directory: {data_dir}")
    print(f"  Total entries: {len(sources)} ({len(multi_date_windows)} dates x {len(kraken_streams)} streams)")


if __name__ == "__main__":
    main()
