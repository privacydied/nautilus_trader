#!/usr/bin/env python3
"""
Cross-exchange funding dispersion carry — CLI entry point.

RESEARCH MEASUREMENT TOOL ONLY. No orders, no execution, no private keys.

Usage:
    python -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry \\
        --mode coverage \\
        --output-dir reports/funding_dispersion_carry

    python -m examples.strategies.venue_agnostic_signal_observer.run_funding_dispersion_carry \\
        --mode run \\
        --binance-btc-archive data/binance_btc_funding.csv \\
        --binance-eth-archive data/binance_eth_funding.csv \\
        --bybit-btc-archive data/bybit_btc_funding.csv \\
        --bybit-eth-archive data/bybit_eth_funding.csv \\
        --output-dir reports/funding_dispersion_carry

Modes:
    coverage   — Print configuration, grid dimensions, and data requirements.
                 Does NOT run the study.
    run        — Run the full Stage 0–8 pipeline against archive data.
                 Produces a verdict and cell-level results.
                 IMPORTANT: the precommitment must be frozen (committed) before
                 the first run. Running against real data before freezing breaks
                 the precommitment property.

The data loading interface (binance/bybit archive read) is a stub that raises
NotImplementedError with a docstring pointing at the data source. This run
script does NOT download archives or make network calls.

Public data observer only. No auth. No orders. No execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .funding_dispersion_carry import (
    ASSETS,
    FROZEN_CELL_COUNT,
    HOLD_LENGTHS,
    NULL_SEED,
    PRIMARY_CAMPAIGN_COST_BPS,
    SAFETY_MODE,
    STUDY_ID,
    THRESHOLDS_BPS,
    VENUES,
    ALLOWED_VERDICTS,
    FORBIDDEN_VERDICTS,
    RunMetadata,
    validate_verdict,
)
from .funding_dispersion_stages import run_pipeline, stage0_load_and_normalize
from .run_artifacts import create_run_id, create_run_dir, atomic_write_json


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-exchange funding dispersion carry — Phase 0 evaluation pipeline",
    )
    parser.add_argument(
        "--mode",
        choices=["coverage", "run"],
        default="coverage",
        help="Pipeline mode: 'coverage' prints config; 'run' executes the pipeline",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: reports/funding_dispersion_carry/<run_id>)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=NULL_SEED,
        help=f"Random seed for null test (default: {NULL_SEED})",
    )
    parser.add_argument(
        "--binance-btc-archive",
        type=str,
        default=None,
        help="Path to Binance BTC funding-rate archive file",
    )
    parser.add_argument(
        "--binance-eth-archive",
        type=str,
        default=None,
        help="Path to Binance ETH funding-rate archive file",
    )
    parser.add_argument(
        "--bybit-btc-archive",
        type=str,
        default=None,
        help="Path to Bybit BTC funding-rate archive file",
    )
    parser.add_argument(
        "--bybit-eth-archive",
        type=str,
        default=None,
        help="Path to Bybit ETH funding-rate archive file",
    )
    return parser


# ---------------------------------------------------------------------------
# Data loading stub — NOT IMPLEMENTED (by design)
# ---------------------------------------------------------------------------


def load_archive_data(
    binance_btc_path: str | None,
    binance_eth_path: str | None,
    bybit_btc_path: str | None,
    bybit_eth_path: str | None,
) -> dict[str, list[tuple[int, float]]]:
    """Load funding-rate archives and return per-series (timestamp_ns, rate) data.

    NOT IMPLEMENTED — this is a stub. The actual data loading must be
    implemented before running the study. Data sources:

    - Binance Vision archive: https://www.binance.com/en/landing/data
      (perpetual / USDⓈ-M funding rates)
    - Bybit historical funding-rate archive:
      https://www.bybit.com/en/crypto-market-data/

    Each series must contain per-settlement funding rates with timestamps.
    Binance and Bybit both settle BTC/ETH perp funding on an ~8h cadence.

    Raises:
        NotImplementedError: Always. Must be implemented before running.
    """
    raise NotImplementedError(
        "Archive data loading is not yet implemented. "
        "Implement this function to read Binance Vision and Bybit historical "
        "funding-rate archives before running the pipeline. "
        "Data sources: "
        "Binance Vision: https://www.binance.com/en/landing/data "
        "Bybit: https://www.bybit.com/en/crypto-market-data/"
    )


# ---------------------------------------------------------------------------
# Coverage mode
# ---------------------------------------------------------------------------


def run_coverage(args: argparse.Namespace) -> int:
    """Print configuration and data requirements. Does NOT run the study."""
    print("=" * 70)
    print(f"  {STUDY_ID} — Coverage Check")
    print("=" * 70)
    print()
    print("FROZEN PARAMETERS")
    print(f"  Assets:             {ASSETS}")
    print(f"  Venues:             {VENUES}")
    print(f"  Thresholds (bps):   {THRESHOLDS_BPS}")
    print(f"  Hold lengths:       {HOLD_LENGTHS}")
    print(f"  Grid dimensions:    {len(ASSETS)} x {len(THRESHOLDS_BPS)} x {len(HOLD_LENGTHS)} = {FROZEN_CELL_COUNT} cells")
    print(f"  Primary cost:       {PRIMARY_CAMPAIGN_COST_BPS} bps")
    print(f"  Diagnostic cost:    {PRIMARY_CAMPAIGN_COST_BPS if False else 6.0} bps (6 bps diagnostic tier)")
    print(f"  Null iterations:    {NULL_SEED} (default seed)")
    print(f"  FDR method:         BY (Benjamini-Yekutieli)")
    print(f"  FDR alpha:          0.05")
    print(f"  FDR family size:    {FROZEN_CELL_COUNT} (frozen)")
    print(f"  Train/holdout:      70/30 chronological split")
    print()
    print("SAFETY")
    print(f"  SAFETY_MODE:        {SAFETY_MODE}")
    print(f"  Allowed verdicts:   {sorted(ALLOWED_VERDICTS)}")
    print(f"  Forbidden verdicts: {sorted(FORBIDDEN_VERDICTS)}")
    print()
    print("DATA REQUIREMENTS")
    print("  Binance BTC perp funding rates (8h cadence)")
    print("  Binance ETH perp funding rates (8h cadence)")
    print("  Bybit BTC perp funding rates (8h cadence)")
    print("  Bybit ETH perp funding rates (8h cadence)")
    print()
    print("PRECOMMITMENT STATUS")
    print("  The precommitment document must be frozen (committed to the project)")
    print("  before the first evaluation run. Running against real data before")
    print("  freezing breaks the precommitment property.")
    print()
    print("=" * 70)
    return 0


# ---------------------------------------------------------------------------
# Run mode
# ---------------------------------------------------------------------------


def run_evaluation(args: argparse.Namespace) -> int:
    """Execute the full Stage 0-8 pipeline. NOT TO BE RUN until precommitment is frozen."""
    # Attempt to load archive data
    try:
        raw_series = load_archive_data(
            args.binance_btc_archive,
            args.binance_eth_archive,
            args.bybit_btc_archive,
            args.bybit_eth_archive,
        )
    except NotImplementedError:
        print("ERROR: Archive data loading is not implemented.", file=sys.stderr)
        print("       Implement load_archive_data() before running the pipeline.", file=sys.stderr)
        print("       The study must NOT be run until the precommitment is frozen.", file=sys.stderr)
        return 1

    # Set up output directory
    base_dir = Path(args.output_dir) if args.output_dir else Path("reports/funding_dispersion_carry")
    run_id = create_run_id(prefix="fdc")
    run_dir = create_run_dir(base_dir, run_id)

    # Run the pipeline
    verdict, cells, metadata = run_pipeline(raw_series, seed=args.seed)

    # Validate verdict
    validate_verdict(verdict)

    # Write outputs
    print(f"\n{'=' * 70}")
    print(f"  STUDY: {STUDY_ID}")
    print(f"  VERDICT: {verdict}")
    print(f"{'=' * 70}")
    print(f"  Run ID: {run_id}")
    print(f"  Output: {run_dir}")
    print(f"  Cells: {len(cells)}")
    if cells:
        verdict_counts = {}
        for c in cells:
            verdict_counts[c.cell_verdict] = verdict_counts.get(c.cell_verdict, 0) + 1
        for v, count in sorted(verdict_counts.items()):
            print(f"    {v}: {count}")
    print(f"  Safety: {SAFETY_MODE}")
    print()

    # Write verdict
    atomic_write_json(run_dir / "verdict.json", {
        "study_id": STUDY_ID,
        "verdict": verdict,
        "run_id": run_id,
        "safety_mode": SAFETY_MODE,
        "generated_at": datetime.now(UTC).isoformat(),
    })

    # Write cell records
    if cells:
        cell_records = [c.to_dict() for c in cells]
        atomic_write_json(run_dir / "grid_evaluation.jsonl", {
            "study_id": STUDY_ID,
            "cell_count": len(cells),
            "cells": cell_records,
        })

    # Write metadata
    if metadata is not None:
        atomic_write_json(run_dir / "metadata.json", metadata.to_dict())

    # IMPORTANT: this script produces a verdict but it MUST NOT be interpreted
    # as a trading signal. The verdict is an archive-level economic signal
    # assessment only.
    print("NOTE: This verdict is an archive-level economic signal assessment.")
    print("      It does NOT constitute a live-trading recommendation.")
    print("      Forbidden verdicts (CANDIDATE_FOR_LIVE, etc.) are blocked by design.")

    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.mode == "coverage":
        return run_coverage(args)
    elif args.mode == "run":
        return run_evaluation(args)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())