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
import logging
import math
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

logger = logging.getLogger(__name__)


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
# Data loading — Binance Vision CSV + Bybit funding archive CSV
# ---------------------------------------------------------------------------

# Binance Vision CSV columns: calc_time, funding_interval_hours, last_funding_rate
# Timestamp is milliseconds since epoch. Rate is a decimal fraction.
BINANCE_COLUMNS = frozenset({"calc_time", "funding_interval_hours", "last_funding_rate"})

# Bybit CSV columns: symbol, fundingRate, fundingRateTimestamp
# Timestamp is milliseconds since epoch. Rate is a decimal fraction string.
BYBIT_COLUMNS = frozenset({"symbol", "fundingRate", "fundingRateTimestamp"})


def _read_binance_funding_csv(path: str) -> list[tuple[int, float]]:
    """Parse a Binance Vision funding-rate CSV.

    Expected columns: calc_time (ms epoch), funding_interval_hours, last_funding_rate.

    Returns list of (timestamp_ns, rate) sorted by timestamp.
    Raises ValueError on missing columns, unparsable rows, duplicate timestamps,
    or non-finite rates.
    """
    import csv

    rows: list[tuple[int, float]] = []
    seen_ts: set[int] = set()

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Binance CSV file is empty: {path}")

        col_names = set(reader.fieldnames)
        missing = BINANCE_COLUMNS - col_names
        if missing:
            raise ValueError(
                f"Binance CSV missing required columns {sorted(missing)}. "
                f"Expected {sorted(BINANCE_COLUMNS)}, got {sorted(col_names)}"
            )

        for line_num, row in enumerate(reader, start=2):
            # Parse timestamp (milliseconds → nanoseconds)
            try:
                ts_ms = int(row["calc_time"])
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"Binance CSV row {line_num}: unparsable calc_time "
                    f"'{row.get('calc_time', '')}': {e}"
                ) from e
            ts_ns = ts_ms * 1_000_000

            # Parse funding rate
            try:
                rate = float(row["last_funding_rate"])
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"Binance CSV row {line_num}: unparsable last_funding_rate "
                    f"'{row.get('last_funding_rate', '')}': {e}"
                ) from e

            if not math.isfinite(rate):
                raise ValueError(
                    f"Binance CSV row {line_num}: non-finite funding rate {rate}"
                )

            if ts_ns in seen_ts:
                raise ValueError(
                    f"Binance CSV row {line_num}: duplicate timestamp {ts_ns} "
                    f"(millis={ts_ms})"
                )
            seen_ts.add(ts_ns)

            rows.append((ts_ns, rate))

    rows.sort()
    return rows


def _read_bybit_funding_csv(path: str) -> list[tuple[int, float]]:
    """Parse a Bybit funding-rate archive CSV.

    Expected columns: symbol, fundingRate, fundingRateTimestamp.
    Timestamp is milliseconds since epoch. Rate is a decimal fraction string.

    Returns list of (timestamp_ns, rate) sorted by timestamp.
    Raises ValueError on missing columns, unparsable rows, duplicate timestamps,
    or non-finite rates.
    """
    import csv

    rows: list[tuple[int, float]] = []
    seen_ts: set[int] = set()

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Bybit CSV file is empty: {path}")

        col_names = set(reader.fieldnames)
        missing = BYBIT_COLUMNS - col_names
        if missing:
            raise ValueError(
                f"Bybit CSV missing required columns {sorted(missing)}. "
                f"Expected {sorted(BYBIT_COLUMNS)}, got {sorted(col_names)}"
            )

        for line_num, row in enumerate(reader, start=2):
            # Parse timestamp (milliseconds → nanoseconds)
            try:
                ts_ms = int(row["fundingRateTimestamp"])
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"Bybit CSV row {line_num}: unparsable fundingRateTimestamp "
                    f"'{row.get('fundingRateTimestamp', '')}': {e}"
                ) from e
            ts_ns = ts_ms * 1_000_000

            # Parse funding rate
            try:
                rate = float(row["fundingRate"])
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f"Bybit CSV row {line_num}: unparsable fundingRate "
                    f"'{row.get('fundingRate', '')}': {e}"
                ) from e

            if not math.isfinite(rate):
                raise ValueError(
                    f"Bybit CSV row {line_num}: non-finite funding rate {rate}"
                )

            if ts_ns in seen_ts:
                raise ValueError(
                    f"Bybit CSV row {line_num}: duplicate timestamp {ts_ns} "
                    f"(millis={ts_ms})"
                )
            seen_ts.add(ts_ns)

            rows.append((ts_ns, rate))

    rows.sort()
    return rows


def load_archive_data(
    binance_btc_path: str | None,
    binance_eth_path: str | None,
    bybit_btc_path: str | None,
    bybit_eth_path: str | None,
) -> dict[str, list[tuple[int, float]]]:
    """Load funding-rate archives and return per-series (timestamp_ns, rate) data.

    Reads Binance Vision CSV files and Bybit funding archive CSV files.
    Each file's rates are returned in their original unit — Stage 0 will
    detect and normalize them to bps.

    Data sources:
    - Binance Vision: https://www.binance.com/en/landing/data
      (perpetual / USDⓈ-M funding rates, monthly zipped CSVs)
    - Bybit historical archive: exported as CSV with columns
      symbol, fundingRate, fundingRateTimestamp

    Returns dict with keys: binance_BTC, binance_ETH, bybit_BTC, bybit_ETH.
    Each value is a sorted list of (timestamp_ns, rate) tuples.

    Raises:
        ValueError: On missing columns, unparsable rows, duplicate timestamps,
            or non-finite rates.
        FileNotFoundError: If any required path is None or doesn't exist.
    """
    required: dict[str, tuple[str | None, str, str]] = {
        "binance_BTC": (binance_btc_path, "binance", "BTC"),
        "binance_ETH": (binance_eth_path, "binance", "ETH"),
        "bybit_BTC": (bybit_btc_path, "bybit", "BTC"),
        "bybit_ETH": (bybit_eth_path, "bybit", "ETH"),
    }

    result: dict[str, list[tuple[int, float]]] = {}

    for key, (path, venue, _asset) in required.items():
        if path is None:
            raise FileNotFoundError(
                f"Required archive path for {key} is None. "
                f"All four archive paths must be provided for run mode."
            )
        filepath = Path(path)
        if not filepath.exists():
            raise FileNotFoundError(f"Archive file not found: {path}")

        if venue == "binance":
            series = _read_binance_funding_csv(path)
        elif venue == "bybit":
            series = _read_bybit_funding_csv(path)
        else:
            raise ValueError(f"Unknown venue: {venue}")

        if not series:
            raise ValueError(f"Archive file {path} contains no valid funding rows")

        result[key] = series
        logger.info(f"Loaded {len(series)} settlements from {key} ({path})")

    return result


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
    # Load archive data — requires all four paths
    try:
        raw_series = load_archive_data(
            args.binance_btc_archive,
            args.binance_eth_archive,
            args.bybit_btc_archive,
            args.bybit_eth_archive,
        )
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("       All four archive paths must be provided for run mode.", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: Archive data validation failed: {e}", file=sys.stderr)
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