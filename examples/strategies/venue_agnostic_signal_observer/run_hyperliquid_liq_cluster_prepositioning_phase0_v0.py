#!/usr/bin/env python3
"""CLI runner for Hyperliquid liquidation-cluster prepositioning Phase 0 v0.

Usage:
    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_liq_cluster_prepositioning_phase0_v0 \\
        --out-root reports/hyperliquid_liq_cluster_prepositioning_phase0_v0 \\
        --data-root data \\
        --dry-run

    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_liq_cluster_prepositioning_phase0_v0 \\
        --out-root reports/hyperliquid_liq_cluster_prepositioning_phase0_v0 \\
        --data-root data \\
        --plan-only

    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_liq_cluster_prepositioning_phase0_v0 \\
        --out-root reports/hyperliquid_liq_cluster_prepositioning_phase0_v0 \\
        --data-root data \\
        --start-date 2025-08-17 \\
        --end-date latest \\
        --max-download-bytes 25000000000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure we can import the core module
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hyperliquid_liq_cluster_prepositioning_phase0_v0 import (
    StudyConfig,
    StudySummary,
    StudyStatus,
    FROZEN_SYMBOLS,
    write_summary_json,
    write_summary_md,
    write_precommitment_hash,
    inventory_data_sources,
    build_leverage_tiers,
    load_asset_ctxs,
    load_l2_books,
    run_phase_minus1,
    run_phase0,
    sha256_file,
    sha256_str,
    _now_utc_iso,
    BYTES_PER_GB,
    STUDY_ID,
)


def get_git_info(repo_root: str) -> tuple[str, str, bool]:
    """Get git SHA, branch, and dirty status."""
    sha = "unknown"
    branch = "unknown"
    dirty = False

    # Try git command
    import subprocess
    try:
        sha = subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()[:12]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        branch = subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    try:
        dirty_out = subprocess.check_output(
            ["git", "-C", repo_root, "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        dirty = bool(dirty_out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return sha, branch, dirty


def parse_args(argv=None):
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Hyperliquid liquidation cluster prepositioning Phase 0 v0 runner",
    )
    parser.add_argument("--out-root", default="reports/hyperliquid_liq_cluster_prepositioning_phase0_v0",
                        help="Output directory root")
    parser.add_argument("--data-root", default="data",
                        help="Data directory root")
    parser.add_argument("--start-date", default="2025-08-17",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="latest",
                        help="End date (YYYY-MM-DD or 'latest')")
    parser.add_argument("--burn-in-days", type=int, default=14,
                        help="Burn-in window in days")
    parser.add_argument("--min-reconstruction-coverage-fraction", type=float, default=0.40,
                        help="Minimum reconstruction coverage fraction")
    parser.add_argument("--max-download-bytes", type=int, default=25 * BYTES_PER_GB,
                        help="Max download bytes (~25 GiB default)")
    parser.add_argument("--allow-s3-archive-read", action="store_true",
                        help="Enable requester-pays S3 reads")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write preview artifacts, no data processing")
    parser.add_argument("--plan-only", action="store_true",
                        help="Inventory data, estimate costs, no processing")
    parser.add_argument("--skip-null", action="store_true",
                        help="Skip circular-shift null (for tests/debug)")
    parser.add_argument("--null-iterations", type=int, default=1000,
                        help="Number of null iterations")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbol override")
    parser.add_argument("--repo-root", default=None,
                        help="Repository root (default: auto-detect)")
    return parser.parse_args(argv)


def main(argv=None):
    """Main entry point."""
    args = parse_args(argv)

    # Determine repo root
    repo_root = args.repo_root or str(Path(__file__).resolve().parent.parent.parent.parent)

    # Handle end_date
    end_date = args.end_date
    if end_date == "latest":
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Parse symbols
    symbols = FROZEN_SYMBOLS
    if args.symbols:
        symbols = tuple(s.strip().upper() for s in args.symbols.split(","))

    # Build config
    config = StudyConfig(
        out_root=args.out_root,
        data_root=args.data_root,
        start_date=args.start_date,
        end_date=end_date,
        burn_in_days=args.burn_in_days,
        min_reconstruction_coverage_fraction=args.min_reconstruction_coverage_fraction,
        max_download_bytes=args.max_download_bytes,
        allow_s3=args.allow_s3_archive_read,
        dry_run=args.dry_run,
        plan_only=args.plan_only,
        symbols=symbols,
        skip_null=args.skip_null,
        null_iterations=args.null_iterations,
        seed=args.seed,
        command_args=sys.argv[1:],
    )

    # Get git info
    git_sha, git_branch, git_dirty = get_git_info(repo_root)

    # Create output directory
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(config.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Precommitment path and hash
    precommitment_path = str(Path(config.out_root).parent / "docs" /
                             "HYPERLIQUID_LIQ_CLUSTER_PREPOSITIONING_PHASE0_V0_PRECOMMITMENT.md")
    precommitment_hash = ""
    if os.path.exists(precommitment_path):
        precommitment_hash = write_precommitment_hash(precommitment_path, str(out_dir / "precommitment_hash.txt"))
    else:
        # Write placeholder
        with open(str(out_dir / "precommitment_hash.txt"), "w") as f:
            f.write("COMPUTED_AFTER_FILE_WRITE")

    print(f"[{STUDY_ID}] Run ID: {run_id}")
    print(f"[{STUDY_ID}] Output: {out_dir}")
    print(f"[{STUDY_ID}] Git SHA: {git_sha}")
    print(f"[{STUDY_ID}] Git Branch: {git_branch}")
    print(f"[{STUDY_ID}] Dry run: {config.dry_run}")
    print(f"[{STUDY_ID}] Plan only: {config.plan_only}")
    print()

    # Phase -1: Reconstruction feasibility
    print("[Phase -1] Running reconstruction feasibility check...")
    t0 = time.time()

    summary, coverage, tiers_dict, positions, liq_levels, excluded = run_phase_minus1(config)

    elapsed_minus1 = time.time() - t0
    print(f"[Phase -1] Status: {summary.status} ({elapsed_minus1:.1f}s)")
    print(f"[Phase -1] Symbols with data: {len(coverage.symbols_with_data)}")
    print(f"[Phase -1] Excluded symbols: {len(excluded)}")

    if summary.status in (
        StudyStatus.PHASE_MINUS1_BLOCKED_NO_INPUT_DATA.name,
        StudyStatus.PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE.name,
    ):
        print(f"[Phase -1] Blocked: {summary.status}")
        summary.git_sha = git_sha
        summary.git_branch = git_branch
        summary.git_dirty = git_dirty
        summary.repo_root = repo_root
        summary.precommitment_path = precommitment_path
        summary.precommitment_sha256 = precommitment_hash

        write_summary_json(summary, str(out_dir / "summary.json"))
        write_summary_md(summary, str(out_dir / "summary.md"))

        # Write inventory artifacts
        with open(str(out_dir / "input_inventory.json"), "w") as f:
            json.dump({
                "study_id": STUDY_ID,
                "run_id": run_id,
                "data_coverage": {
                    "asset_ctxs_available": coverage.asset_ctxs_available,
                    "l2book_available": coverage.l2book_available,
                    "node_fills_available": coverage.node_fills_available,
                    "symbols_with_data": coverage.symbols_with_data,
                },
            }, f, indent=2)

        with open(str(out_dir / "schema_inventory.json"), "w") as f:
            json.dump({
                "study_id": STUDY_ID,
                "run_id": run_id,
                "asset_ctxs_keys": ["index_price", "open_interest", "price", "price_source", "symbol", "ts_event"],
                "l2book_columns": ["ts_event", "coin", "seq", "bid_px_0..19", "bid_sz_0..19", "bid_n_0..19",
                                   "ask_px_0..19", "ask_sz_0..19", "ask_n_0..19"],
            }, f, indent=2)

        with open(str(out_dir / "coverage_inventory.json"), "w") as f:
            json.dump({
                "study_id": STUDY_ID,
                "run_id": run_id,
                "symbols_with_data": coverage.symbols_with_data,
                "excluded_symbols": excluded,
            }, f, indent=2)

        print(f"[Phase -1] Blocked artifacts written to {out_dir}")
        return 1

    if config.dry_run:
        # Dry run: write preview artifacts without running Phase 0
        summary.status = StudyStatus.PHASE_MINUS1_DRY_RUN_READY.name
        summary.git_sha = git_sha
        summary.git_branch = git_branch
        summary.git_dirty = git_dirty
        summary.repo_root = repo_root
        summary.precommitment_path = precommitment_path
        summary.precommitment_sha256 = precommitment_hash

        write_summary_json(summary, str(out_dir / "summary.json"))
        write_summary_md(summary, str(out_dir / "summary.md"))
        print(f"[Phase -1] Dry run artifacts written to {out_dir}")
        return 0

    # Write leverage tier snapshot
    tiers_list = list(tiers_dict.values())
    with open(str(out_dir / "leverage_tier_snapshot.json"), "w") as f:
        json.dump({
            "study_id": STUDY_ID,
            "run_id": run_id,
            "tiers": [
                {"symbol": t.symbol, "max_leverage": t.max_leverage,
                 "maintenance_margin_fraction": t.maintenance_margin_fraction,
                 "source": t.source}
                for t in tiers_list
            ],
        }, f, indent=2)

    # Write reconstruction audit
    with open(str(out_dir / "reconstruction_audit.json"), "w") as f:
        json.dump({
            "study_id": STUDY_ID,
            "run_id": run_id,
            "symbols_reconstructed": list(positions.keys()),
            "total_positions": sum(len(p) for p in positions.values()),
            "total_liq_levels": sum(len(l) for l in liq_levels.values()),
            "excluded_symbols": excluded,
        }, f, indent=2)

    # Plan-only mode: stop after Phase -1
    if config.plan_only:
        print(f"[Plan] Plan-only mode. Stopping after Phase -1.")
        summary.git_sha = git_sha
        summary.git_branch = git_branch
        summary.git_dirty = git_dirty
        summary.repo_root = repo_root
        summary.precommitment_path = precommitment_path
        summary.precommitment_sha256 = precommitment_hash
        summary.status = StudyStatus.PHASE_MINUS1_READY.name

        write_summary_json(summary, str(out_dir / "summary.json"))
        write_summary_md(summary, str(out_dir / "summary.md"))
        return 0

    # Phase 0: Return evaluation
    print()
    print("[Phase 0] Running return evaluation...")
    t0 = time.time()

    # Load L2 books (may be slow for large archives)
    l2_books = {}
    if coverage.l2book_available:
        print("[Phase 0] Loading L2 books...")
        l2_books = load_l2_books(config.data_root, config.symbols)
        print(f"[Phase 0] L2 books loaded for {len(l2_books)} symbols")

    summary = run_phase0(config, positions, liq_levels, {}, l2_books)

    elapsed_phase0 = time.time() - t0
    print(f"[Phase 0] Status: {summary.status} ({elapsed_phase0:.1f}s)")
    print(f"[Phase 0] Total signals: {summary.phase0a.get('total_signals', 0)}")
    print(f"[Phase 0] Events: {summary.phase0a.get('total_events', 0)}")
    print(f"[Phase 0] Mean net50: {summary.phase0b.get('mean_net50_bps', 0):.2f} bps")
    print(f"[Phase 0] Median net50: {summary.phase0b.get('median_net50_bps', 0):.2f} bps")
    print(f"[Phase 0] Win rate: {summary.phase0b.get('win_rate_net50', 0):.2%}")

    # Write all artifacts
    summary.git_sha = git_sha
    summary.git_branch = git_branch
    summary.git_dirty = git_dirty
    summary.repo_root = repo_root
    summary.precommitment_path = precommitment_path
    summary.precommitment_sha256 = precommitment_hash

    write_summary_json(summary, str(out_dir / "summary.json"))
    write_summary_md(summary, str(out_dir / "summary.md"))

    # Write inventory artifacts
    with open(str(out_dir / "input_inventory.json"), "w") as f:
        json.dump({
            "study_id": STUDY_ID,
            "run_id": run_id,
            "data_coverage": {
                "asset_ctxs_available": coverage.asset_ctxs_available,
                "l2book_available": coverage.l2book_available,
                "node_fills_available": coverage.node_fills_available,
                "symbols_with_data": coverage.symbols_with_data,
                "date_range": f"{coverage.date_range_start} to {coverage.date_range_end}",
            },
        }, f, indent=2)

    with open(str(out_dir / "schema_inventory.json"), "w") as f:
        json.dump({
            "study_id": STUDY_ID,
            "run_id": run_id,
            "asset_ctxs_keys": ["index_price", "open_interest", "price", "price_source", "symbol", "ts_event"],
            "l2book_columns": ["ts_event", "coin", "seq", "bid_px_0..19", "bid_sz_0..19", "bid_n_0..19",
                               "ask_px_0..19", "ask_sz_0..19", "ask_n_0..19"],
        }, f, indent=2)

    with open(str(out_dir / "coverage_inventory.json"), "w") as f:
        json.dump({
            "study_id": STUDY_ID,
            "run_id": run_id,
            "symbols_with_data": coverage.symbols_with_data,
            "excluded_symbols": excluded,
        }, f, indent=2)

    print(f"\n[{STUDY_ID}] All artifacts written to {out_dir}")
    print(f"[{STUDY_ID}] Final status: {summary.status}")

    # Print summary.md content
    print()
    md_path = str(out_dir / "summary.md")
    if os.path.exists(md_path):
        with open(md_path) as f:
            print(f.read())

    return 0


if __name__ == "__main__":
    sys.exit(main())
