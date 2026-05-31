#!/usr/bin/env python3
"""CLI runner for Hyperliquid node fills liquidation reconstruction Phase -1 v0 probe.

Usage:
    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --out-root reports/hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --data-root data \\
      --dry-run

    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --out-root reports/hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --data-root data \\
      --plan-only

    # Remote plan/list only:
    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --out-root reports/hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --data-root data \\
      --include-remote-plan \\
      --allow-s3-archive-read \\
      --requester-pays \\
      --plan-only \\
      --max-download-bytes 100000000

    # Tiny measured object fetch:
    uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --out-root reports/hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 \\
      --data-root data \\
      --preferred-symbol SOL \\
      --max-hours 6 \\
      --allow-s3-archive-read \\
      --requester-pays \\
      --max-download-bytes 100000000
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure the project root is on sys.path for imports
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from examples.strategies.venue_agnostic_signal_observer import (  # noqa: E402
    hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0 as probe_mod,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Hyperliquid node fills liquidation reconstruction Phase -1 v0 probe",
    )

    # Required
    parser.add_argument("--out-root", required=True, help="Root directory for output artifacts")
    parser.add_argument("--data-root", default=None, help="Local data root for cached archives")

    # Symbol selection
    parser.add_argument("--preferred-symbol", default="SOL", help="Preferred symbol to focus on")
    parser.add_argument("--fallback-symbols", default="DOGE,LINK,AVAX,ADA",
                        help="Comma-separated fallback symbols")

    # Date range
    parser.add_argument("--start-date", default="", help="Start date (YYYY-MM-DD), auto if empty")
    parser.add_argument("--end-date", default="", help="End date (YYYY-MM-DD), auto if empty")

    # Modes
    parser.add_argument("--dry-run", action="store_true", help="Dry run — no S3, no download")
    parser.add_argument("--plan-only", action="store_true", help="Plan only — list but do not download")
    parser.add_argument("--include-remote-plan", action="store_true",
                        help="Include remote S3 listing in plan phase")
    parser.add_argument("--leverage-source-plan-only", action="store_true",
                        help="Run leverage-source discovery without downloading fills")

    # S3 / requester-pays
    parser.add_argument("--allow-s3-archive-read", action="store_true",
                        help="Allow S3 archive reads (requester-pays)")
    parser.add_argument("--requester-pays", action="store_true",
                        help="Treat S3 as requester-pays bucket")

    # Caching
    parser.add_argument("--cache-only", action="store_true", default=False,
                        help="Use only local cache (default: false — S3 allowed)")

    # Limits
    parser.add_argument("--max-download-bytes", type=int, default=100_000_000,
                        help="Hard cap on download bytes (default: 100MB)")
    parser.add_argument("--schema-sample-limit", type=int, default=10_000,
                        help="Max fill records to sample for schema validation")
    parser.add_argument("--max-hours", type=int, default=1,
                        help="Max hours of data to download in thin slice (default: 1)")

    # OI completeness
    parser.add_argument("--min-oi-coverage-fraction", type=float, default=0.40,
                        help="Minimum median OI coverage fraction for burn-in gate")
    parser.add_argument("--burn-in-days", type=int, default=0,
                        help="Burn-in days for completeness probe (0 = thin-slice diagnostics)")

    # Leverage
    parser.add_argument("--leverage-mode", default="exact_required",
                        choices=["exact_required", "max_bound_diagnostic"],
                        help="Leverage mode: exact or max bound diagnostic")
    parser.add_argument("--bound-diagnostic", action="store_true",
                        help="Enable max-leverage bound diagnostic mode")

    # Wall 2 margin-mode kill-test
    parser.add_argument("--wall2-margin-mode-killtest", action="store_true",
                        help="Run Wall 2 margin-mode kill-test after Wall 1 passes")
    parser.add_argument("--wall2-update-leverage-source-probe", action="store_true",
                        help="Run Wall 2 updateLeverage source-existence probe")
    parser.add_argument("--wall2-targeted-holder-leverage-lookup", action="store_true",
                        help="Run Wall 2 targeted big-holder backward leverage lookup")

    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> probe_mod.StudyConfig:
    """Build StudyConfig from CLI args."""
    fallback_symbols = tuple(s.strip().upper() for s in args.fallback_symbols.split(","))

    # Auto date logic: if not specified, use today as start, no end (plan only)
    start_date = args.start_date or ""
    end_date = args.end_date or ""

    return probe_mod.StudyConfig(
        out_root=args.out_root,
        data_root=args.data_root,
        preferred_symbol=args.preferred_symbol.upper(),
        fallback_symbols=fallback_symbols,
        start_date=start_date,
        end_date=end_date,
        dry_run=args.dry_run,
        plan_only=args.plan_only,
        include_remote_plan=args.include_remote_plan,
        allow_s3_archive_read=args.allow_s3_archive_read,
        requester_pays=args.requester_pays,
        cache_only=args.cache_only,
        max_download_bytes=args.max_download_bytes,
        schema_sample_limit=args.schema_sample_limit,
        max_hours=args.max_hours,
        min_oi_coverage_fraction=args.min_oi_coverage_fraction,
        burn_in_days=args.burn_in_days,
        leverage_mode=args.leverage_mode,
        bound_diagnostic=args.bound_diagnostic,
        leverage_source_plan_only=args.leverage_source_plan_only,
        wall2_margin_mode_killtest=args.wall2_margin_mode_killtest,
        wall2_update_leverage_source_probe=args.wall2_update_leverage_source_probe,
        wall2_targeted_holder_leverage_lookup=args.wall2_targeted_holder_leverage_lookup,
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    print(f"Phase -1 v0 probe starting (orjson={'yes' if probe_mod.HAS_ORJSON else 'no'})", flush=True)

    args = parse_args(argv)
    config = build_config(args)

    # Run the probe
    try:
        summary = probe_mod.run_probe(config)
        print(f"\nFinal status: {summary.status}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
        return 130
    except Exception as exc:
        print(f"\nProbe failed: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
