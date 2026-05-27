#!/usr/bin/env python3
"""CLI runner for HIP-3 Builder-DEX TradFi Off-Hours Scout v0."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import asdict

# Import the core scout
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_offhours_scout_v0 import (
    ScoutConfig, run_scout, _config_hash, _get_git_info, _now_utc, _write_json,
    _make_artifact_base, ALL_SEED_TICKERS, DiscoveryStatus, STUDY_ID,
)

UTC = timezone.utc


def main():
    parser = argparse.ArgumentParser(
        description="HIP-3 Builder-DEX TradFi Off-Hours Scout v0 — CLI Runner")
    parser.add_argument("--out-root", default="reports/hip3_builder_dex_tradfi_offhours_scout_v0")
    parser.add_argument("--seed-tickers", default=",".join(ALL_SEED_TICKERS))
    parser.add_argument("--start-date", default="2025-10-13")
    parser.add_argument("--end-date", default="latest")
    parser.add_argument("--max-symbols", type=int, default=12)
    parser.add_argument("--max-archive-days-per-symbol", type=int, default=7)
    parser.add_argument("--max-l2-hours-per-symbol", type=int, default=72)
    parser.add_argument("--download-budget-bytes", type=int, default=500_000_000)
    parser.add_argument("--l2-budget-bytes", type=int, default=250_000_000)
    parser.add_argument("--require-sanity-seeds", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-network-public", action="store_true")
    parser.add_argument("--allow-s3-archive-read", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = ScoutConfig(
        out_root=args.out_root,
        seed_tickers=args.seed_tickers.split(","),
        start_date=args.start_date,
        end_date=args.end_date,
        max_symbols=args.max_symbols,
        max_archive_days_per_symbol=args.max_archive_days_per_symbol,
        max_l2_hours_per_symbol=args.max_l2_hours_per_symbol,
        download_budget_bytes=args.download_budget_bytes,
        l2_budget_bytes=args.l2_budget_bytes,
        require_sanity_seeds=args.require_sanity_seeds,
        allow_network_public=args.allow_network_public,
        allow_s3_archive_read=args.allow_s3_archive_read,
        dry_run=args.dry_run,
    )

    if config.dry_run:
        git_sha, git_dirty = _get_git_info()
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        config.run_id = f"{ts}_{_config_hash(config)[:8]}"
        run_dir = Path(config.out_root) / config.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        preview = _make_artifact_base(config.run_id, config, git_sha, git_dirty, "dry_run",
                                       DiscoveryStatus.SCOUT_READY.value)
        preview["frontend_api_consistency_check_skipped"] = True
        preview["config_hash"] = _config_hash(config)
        _write_json(run_dir / "dry_run_preview.json", preview)
        print(json.dumps({"status": "HIP3_BUILDER_DEX_SCOUT_READY", "run_dir": str(run_dir)}, indent=2))
        return

    result = run_scout(config)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
