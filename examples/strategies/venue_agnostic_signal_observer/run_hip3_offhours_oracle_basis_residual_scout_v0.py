#!/usr/bin/env python3
"""
CLI runner for HIP-3 off-hours oracle basis residual scout v0.

Phase -1 feasibility scout for HIP-3 equity/index-like perps on Hyperliquid.
Six-gate kill-chain: symbol discovery → archive coverage → oracle
classification → fee discovery → L2 liquidity → residual basis tail.

Usage:
  uv run python examples/strategies/venue_agnostic_signal_observer/run_hip3_offhours_oracle_basis_residual_scout_v0.py \\
      --allow-network-public --allow-s3-archive-read \\
      --start-date 2025-10-13

  uv run python examples/strategies/venue_agnostic_signal_observer/run_hip3_offhours_oracle_basis_residual_scout_v0.py \\
      --dry-run

This runner imports NO network libraries directly. All network calls
go through the scout module's network chokepoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer.hip3_offhours_oracle_basis_residual_scout_v0 import (
    STUDY_ID,
    DEFAULT_OUT_ROOT,
    DEFAULT_START_DATE,
    DEFAULT_MAX_SYMBOLS,
    DEFAULT_MIN_COVERAGE_DAYS,
    DEFAULT_MIN_TAIL_EVENTS,
    DEFAULT_MAX_L2_HOURS_PER_SYMBOL,
    DEFAULT_SAMPLE_L2_DAYS,
    DEFAULT_DOWNLOAD_BUDGET_BYTES,
    DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES,
    run_scout,
    write_scout_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"HIP-3 Off-Hours Oracle Basis Residual Scout v0 ({STUDY_ID})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Gate chain: symbol discovery -> archive coverage -> oracle classification ->
fee discovery -> L2 liquidity -> residual basis tail.

Each gate short-circuits the run. Scout output includes only artifacts
reached by the gate chain, plus summary and manifest.

This is a Phase -1 feasibility scout. It does NOT:
- Evaluate strategy PnL
- Authorize Phase 0, paper, or live execution
- Mutate REJECTED_RESEARCH.md
- Use private keys, auth, orders, or account access
        """,
    )

    # Output
    parser.add_argument(
        "--out-root",
        default=DEFAULT_OUT_ROOT,
        help=f"Output root directory (default: {DEFAULT_OUT_ROOT})",
    )

    # Date range
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE.isoformat(),
        help=f"Start date YYYY-MM-DD (default: {DEFAULT_START_DATE.isoformat()})",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date YYYY-MM-DD (default: today UTC)",
    )

    # Symbol control
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=DEFAULT_MAX_SYMBOLS,
        help=f"Max symbols to deeply analyze (default: {DEFAULT_MAX_SYMBOLS})",
    )
    parser.add_argument(
        "--prefer-index-like",
        action="store_true",
        default=True,
        dest="prefer_index_like",
        help="Prefer index-like symbols over single-stock-like (default: True)",
    )
    parser.add_argument(
        "--no-prefer-index-like",
        action="store_false",
        dest="prefer_index_like",
        help="Do not prefer index-like symbols",
    )

    # L2 sampling
    parser.add_argument(
        "--sample-l2-days",
        type=int,
        default=DEFAULT_SAMPLE_L2_DAYS,
        help=f"Days to sample L2 archives (default: {DEFAULT_SAMPLE_L2_DAYS})",
    )
    parser.add_argument(
        "--max-l2-hours-per-symbol",
        type=int,
        default=DEFAULT_MAX_L2_HOURS_PER_SYMBOL,
        help=f"Max L2 hours per symbol (default: {DEFAULT_MAX_L2_HOURS_PER_SYMBOL})",
    )
    parser.add_argument(
        "--skip-l2-download",
        action="store_true",
        default=False,
        help="Skip L2 archive download entirely",
    )

    # Mode
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Dry-run: no network calls, cached discovery only",
    )

    # Network flags
    parser.add_argument(
        "--allow-network-public",
        action="store_true",
        default=False,
        help="Allow public Hyperliquid endpoint calls",
    )
    parser.add_argument(
        "--allow-s3-archive-read",
        action="store_true",
        default=False,
        help="Allow S3 archive reads (implies requester-pays acknowledgement)",
    )

    # Anchor source
    parser.add_argument(
        "--anchor-source",
        choices=["cme_futures_proxy", "cash_eod_only", "none"],
        default="cme_futures_proxy",
        help="Fair-value anchor source (default: cme_futures_proxy)",
    )

    # Coverage thresholds
    parser.add_argument(
        "--min-coverage-days",
        type=int,
        default=DEFAULT_MIN_COVERAGE_DAYS,
        help=f"Min archive coverage days (default: {DEFAULT_MIN_COVERAGE_DAYS})",
    )
    parser.add_argument(
        "--min-tail-events",
        type=int,
        default=DEFAULT_MIN_TAIL_EVENTS,
        help=f"Min tail events for pass (default: {DEFAULT_MIN_TAIL_EVENTS})",
    )

    # Download budgets
    parser.add_argument(
        "--download-budget-bytes",
        type=int,
        default=DEFAULT_DOWNLOAD_BUDGET_BYTES,
        help=f"Total download budget in bytes (default: {DEFAULT_DOWNLOAD_BUDGET_BYTES})",
    )
    parser.add_argument(
        "--per-symbol-l2-budget-bytes",
        type=int,
        default=DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES,
        help=f"Per-symbol L2 budget in bytes (default: {DEFAULT_PER_SYMBOL_L2_BUDGET_BYTES})",
    )

    return parser


def parse_date(s: str | None) -> date | None:
    if s is None:
        return None
    return date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Build args dict for scout
    scout_args: dict[str, Any] = {
        "out_root": args.out_root,
        "start_date": parse_date(args.start_date) or DEFAULT_START_DATE,
        "end_date": parse_date(args.end_date),
        "max_symbols": args.max_symbols,
        "prefer_index_like": args.prefer_index_like,
        "sample_l2_days": args.sample_l2_days,
        "max_l2_hours_per_symbol": args.max_l2_hours_per_symbol,
        "skip_l2_download": args.skip_l2_download,
        "dry_run": args.dry_run,
        "allow_network_public": args.allow_network_public,
        "allow_s3_archive_read": args.allow_s3_archive_read,
        "anchor_source": args.anchor_source,
        "min_coverage_days": args.min_coverage_days,
        "min_tail_events": args.min_tail_events,
        "download_budget_bytes": args.download_budget_bytes,
        "per_symbol_l2_budget_bytes": args.per_symbol_l2_budget_bytes,
        "repo_root": Path(__file__).resolve().parent.parent.parent.parent.parent,
    }

    # Run the scout
    result = run_scout(scout_args)

    # Write artifacts
    run_id = result.run_id
    artifacts = write_scout_artifacts(
        result,
        out_root=args.out_root,
        run_id=run_id,
        dry_run=args.dry_run,
    )

    # Print final status line
    final_line = f"HIP3_SCOUT_STATUS={result.status}"
    if result.gate_failed_at:
        final_line += f" GATE_FAIL={result.gate_failed_at}"
    if result.kill_reason:
        final_line += f" REASON={result.kill_reason}"
    print(final_line)

    # Print artifact paths
    if args.dry_run:
        preview_path = artifacts.get("dry_run_preview.json", "")
        if preview_path:
            print(f"Preview: {preview_path}")
    else:
        run_dir_path = str(Path(args.out_root) / run_id)
        print(f"Report directory: {run_dir_path}")

    status_str = str(result.status)
    is_error = status_str == "HIP3_SCOUT_ERROR"
    return 0 if not is_error else 1


if __name__ == "__main__":
    sys.exit(main())