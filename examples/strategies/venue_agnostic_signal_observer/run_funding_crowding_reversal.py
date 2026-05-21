"""
CLI runner for Family 2 funding crowding reversal evaluation.

RESEARCH MEASUREMENT TOOL ONLY. No orders, no execution, no private keys.

Two modes:
  --mode coverage (default): fetch archive data, report coverage, propose window.
  --mode run (future): execute the frozen 60-cell evaluation (separate task).

Usage (coverage mode):

    python examples/strategies/venue_agnostic_signal_observer/run_funding_crowding_reversal.py
        --mode coverage
        --cache-dir data/funding_crowding_cache
        --start-year 2020 --start-month 1
        --end-year 2026 --end-month 4

This task only supports coverage mode.
The real evaluation run is a separate future task.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .funding_crowding_data import ArchiveCache
from .funding_crowding_data import FundingRateRow
from .funding_crowding_data import SpotKlineRow
from .funding_crowding_data import compute_funding_coverage
from .funding_crowding_data import fetch_funding_range
from .funding_crowding_data import fetch_spot_klines_range
from .funding_crowding_evaluation import FundingObservation
from .funding_crowding_evaluation import SpotPriceSnapshot
from .funding_crowding_evaluation import estimate_coverage


# ---------------------------------------------------------------------------
# Archive probe helpers
# ---------------------------------------------------------------------------

FUNDING_URL_TEMPLATE: str = (
    "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
    "BTCUSDT/BTCUSDT-fundingRate-{year}-{month:02d}.zip"
)

SPOT_URL_TEMPLATE: str = (
    "https://data.binance.vision/data/spot/monthly/klines/"
    "BTCUSDT/1h/BTCUSDT-1h-{year}-{month:02d}.zip"
)


def _probe_archive_availability(
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> list[dict[str, Any]]:
    """
    Probe Binance Vision archive months and classify each.

    Returns list of dicts:
        {year, month, funding_status, spot_status}
        where status is one of: "available", "missing" (HTTP 404),
        "failed" (network/HTTP error).
    """
    results: list[dict[str, Any]] = []
    year, month = start_year, start_month
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        while (year, month) <= (end_year, end_month):
            f_url = FUNDING_URL_TEMPLATE.format(year=year, month=month)
            s_url = SPOT_URL_TEMPLATE.format(year=year, month=month)
            f_status: str = "?"
            s_status: str = "?"
            try:
                r = client.head(f_url)
                f_status = "available" if r.status_code == 200 else "missing"
            except Exception:
                f_status = "failed"
            try:
                r = client.head(s_url)
                s_status = "available" if r.status_code == 200 else "missing"
            except Exception:
                s_status = "failed"
            results.append({
                "year": year,
                "month": month,
                "funding_status": f_status,
                "spot_status": s_status,
            })
            month += 1
            if month > 12:
                month = 1
                year += 1
    return results


def _find_latest_available(probe_results: list[dict[str, Any]], data_type: str) -> tuple[int, int] | None:
    """Find the latest month where *data_type* status is 'available'."""
    key = f"{data_type}_status"
    latest = None
    for r in probe_results:
        if r.get(key) == "available":
            latest = (r["year"], r["month"])
    return latest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_ns_dt(ts_ns: int | None) -> str:
    """Format a nanosecond timestamp as a human-readable UTC datetime."""
    if ts_ns is None:
        return "N/A"
    dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _funding_rows_to_observations(
    rows: list[FundingRateRow],
) -> list[FundingObservation]:
    return [
        FundingObservation(
            timestamp_ns=r.timestamp_ns,
            funding_rate=r.funding_rate,
            interval_hours=r.interval_hours,
        )
        for r in rows
    ]


def _spot_rows_to_snapshots(
    rows: list[SpotKlineRow],
) -> list[SpotPriceSnapshot]:
    """Convert spot klines to price snapshots, filtering out zero-timestamp rows."""
    result = []
    for r in rows:
        if r.close_time_ns > 1_000_000_000:  # filter out zero/near-zero timestamps
            result.append(
                SpotPriceSnapshot(
                    timestamp_ns=r.close_time_ns,
                    price=r.close_price,
                )
            )
    return result


def _month_label(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


# ---------------------------------------------------------------------------
# Coverage mode
# ---------------------------------------------------------------------------


def run_coverage(args: argparse.Namespace) -> int:
    """Fetch archive data and produce coverage proposal."""
    req_start = _month_label(args.start_year, args.start_month)
    req_end = _month_label(args.end_year, args.end_month)

    print("=" * 70)
    print("FAMILY 2 — FUNDING CROWDING REVERSAL  (coverage mode)")
    print("Evaluation runner: observer-only research tool.")
    print("=" * 70)
    print()
    print(f"  Cache dir        : {args.cache_dir}")
    print(f"  Requested range   : {req_start} to {req_end}")
    print()

    # ------------------------------------------------------------------
    # Step 0: Probe archive availability
    # ------------------------------------------------------------------
    print("[0] Probing Binance Vision archive availability …")
    probe_results = _probe_archive_availability(
        args.start_year, args.start_month,
        args.end_year, args.end_month,
    )

    latest_funding = _find_latest_available(probe_results, "funding")
    latest_spot = _find_latest_available(probe_results, "spot")

    missing_funding = [r for r in probe_results if r["funding_status"] == "missing"]
    missing_spot = [r for r in probe_results if r["spot_status"] == "missing"]
    failed_funding = [r for r in probe_results if r["funding_status"] == "failed"]
    failed_spot = [r for r in probe_results if r["spot_status"] == "failed"]

    # Determine latest checked month
    if probe_results:
        last = probe_results[-1]
        latest_checked = _month_label(last["year"], last["month"])
    else:
        latest_checked = "N/A"

    print(f"    Latest checked month      : {latest_checked}")
    print(f"    Latest available funding  : {_month_label(*latest_funding) if latest_funding else 'NONE'}")
    print(f"    Latest available spot     : {_month_label(*latest_spot) if latest_spot else 'NONE'}")
    if missing_funding:
        print(f"    Missing funding months    : {len(missing_funding)}")
        for r in missing_funding[:5]:
            print(f"      {_month_label(r['year'], r['month'])}")
        if len(missing_funding) > 5:
            print(f"      ... and {len(missing_funding)-5} more")
    if failed_funding:
        print(f"    FAILED funding months     : {len(failed_funding)}")
    if missing_spot:
        print(f"    Missing spot months       : {len(missing_spot)}")
    if failed_spot:
        print(f"    FAILED spot months        : {len(failed_spot)}")

    # Determine latest common usable month
    if latest_funding and latest_spot:
        latest_common = (
            min(latest_funding[0], latest_spot[0]),
            min(latest_funding[1], latest_spot[1]),
        )
        # Handle month rollover if one is earlier
        if latest_funding < latest_spot:
            latest_common = latest_funding
        else:
            latest_common = latest_spot
    else:
        latest_common = None
    latest_common_label = _month_label(*latest_common) if latest_common else "N/A"

    # Earliest common available month
    earliest_funding_avail = None
    earliest_spot_avail = None
    for r in probe_results:
        if r["funding_status"] == "available" and earliest_funding_avail is None:
            earliest_funding_avail = (r["year"], r["month"])
        if r["spot_status"] == "available" and earliest_spot_avail is None:
            earliest_spot_avail = (r["year"], r["month"])
        if earliest_funding_avail and earliest_spot_avail:
            break

    if earliest_funding_avail and earliest_spot_avail:
        earliest_common = (
            max(earliest_funding_avail[0], earliest_spot_avail[0]),
            max(earliest_funding_avail[1], earliest_spot_avail[1]),
        )
        if earliest_funding_avail > earliest_spot_avail:
            earliest_common = earliest_funding_avail
        else:
            earliest_common = earliest_spot_avail
    else:
        earliest_common = None
    earliest_common_label = _month_label(*earliest_common) if earliest_common else "N/A"

    print(f"    Earliest common available : {earliest_common_label}")
    print(f"    Latest common available   : {latest_common_label}")
    print()

    # ------------------------------------------------------------------
    # Step 1: Fetch archive data for actually available months
    # ------------------------------------------------------------------
    print("[1] Fetching BTCUSDT funding rate archives from Binance Vision …")
    print("    (This may take a while for multi-year ranges.)")
    if latest_funding:
        fetch_end_year, fetch_end_month = latest_funding
    else:
        fetch_end_year, fetch_end_month = args.end_year, args.end_month

    cache = ArchiveCache(args.cache_dir)

    funding_rows, funding_hashes = fetch_funding_range(
        cache=cache,
        symbol="BTCUSDT",
        start_year=args.start_year,
        start_month=args.start_month,
        end_year=fetch_end_year,
        end_month=fetch_end_month,
    )
    print(f"    Fetched {len(funding_rows)} funding observations")
    print(f"    Content hashes: {len(funding_hashes)} archive files")
    print()

    print("[2] Fetching BTCUSDT 1h spot kline archives from Binance Vision …")
    if latest_spot:
        spot_end_year, spot_end_month = latest_spot
    else:
        spot_end_year, spot_end_month = args.end_year, args.end_month
    spot_rows, spot_hashes = fetch_spot_klines_range(
        cache=cache,
        symbol="BTCUSDT",
        start_year=args.start_year,
        start_month=args.start_month,
        end_year=spot_end_year,
        end_month=spot_end_month,
    )
    print(f"    Fetched {len(spot_rows)} spot klines")
    print(f"    Content hashes: {len(spot_hashes)} archive files")
    print()

    # Merge content hashes
    all_hashes: dict[str, str] = {}
    all_hashes.update(funding_hashes)
    all_hashes.update(spot_hashes)

    spot_snaps = _spot_rows_to_snapshots(spot_rows)
    funding_obs = _funding_rows_to_observations(funding_rows)

    print("[3] Computing coverage statistics …")
    coverage = estimate_coverage(
        funding_rows=funding_obs,
        spot_prices=spot_snaps,
        content_hashes=all_hashes,
    )

    # Additional funding-specific coverage
    funding_cov = compute_funding_coverage(funding_rows, symbol="BTCUSDT")

    print()
    print("=" * 70)
    print("DATA COVERAGE REPORT")
    print("=" * 70)
    print()
    print("  Requested range:")
    print(f"    Start : {req_start}")
    print(f"    End   : {req_end}")
    print()
    print("  Archive probe:")
    print(f"    Latest checked         : {latest_checked}")
    print(f"    Available funding      : {earliest_common_label} to {_month_label(*latest_funding) if latest_funding else 'N/A'}")
    print(f"    Available spot         : {earliest_common_label} to {_month_label(*latest_spot) if latest_spot else 'N/A'}")
    print(f"    Latest common usable   : {latest_common_label}")
    print(f"    Missing funding months : {len(missing_funding)}")
    print(f"    Failed funding months  : {len(failed_funding)}")
    print(f"    Missing spot months    : {len(missing_spot)}")
    print(f"    Failed spot months     : {len(failed_spot)}")
    print()
    print("  Funding data:")
    print("    Symbol        : BTCUSDT USDⓈ-M")
    print(f"    Observations  : {coverage.total_funding_obs:,}")
    print(f"    Earliest      : {_fmt_ns_dt(coverage.earliest_funding_ns)}")
    print(f"    Latest        : {_fmt_ns_dt(coverage.latest_funding_ns)}")
    print(f"    Year-months   : {len(funding_cov.year_months_fetched)}")
    print()
    print("  Spot price data (1h):")
    print("    Symbol        : BTCUSDT")
    print(f"    Observations  : {len(spot_snaps):,}")
    print(f"    Earliest      : {_fmt_ns_dt(spot_snaps[0].timestamp_ns if spot_snaps else None)}")
    print(f"    Latest        : {_fmt_ns_dt(spot_snaps[-1].timestamp_ns if spot_snaps else None)}")
    print()
    print("  Source paths verified:")
    print("    Funding : /data/futures/um/monthly/fundingRate/BTCUSDT/ (USDⓈ-M)")
    print("    Spot    : /data/spot/monthly/klines/BTCUSDT/1h/ (spot monthly klines)")
    print()
    print("  Funding interval metadata: AVAILABLE (funding_interval_hours column")
    print("    present in Binance Vision archive CSVs)")
    print()
    print("  Coverage estimates (for reference only, NOT used for window choice):")
    print(f"    Cells with any past-only history : {coverage.cells_with_past_only_history}/60")
    print(f"    Cells with >= 50 events          : {coverage.cells_with_ge_50_events}/60")
    print(f"    Cells with >= 100 events         : {coverage.cells_with_ge_100_events}/60")
    print()

    print(f"  Cached files: {len(coverage.content_hashes)}")
    for url, ch in sorted(coverage.content_hashes.items())[:6]:
        fname = url.rsplit("/", 1)[-1]
        print(f"    {fname:40s}  SHA-256: {ch[:16]}...")
    if len(coverage.content_hashes) > 6:
        print(f"    ... and {len(coverage.content_hashes) - 6} more")
    print()

    # ------------------------------------------------------------------
    # Mechanical window proposal
    # ------------------------------------------------------------------
    print("-" * 70)
    print("PROPOSED EVALUATION WINDOW  (mechanical, NOT optimized for cell counts)")
    print("-" * 70)
    print()

    # Window is determined mechanically by:
    # 1. Earliest common available data
    # 2. + 180-day percentile warmup
    # 3. Latest common available data
    # Cell counts are reported but never used to choose the window.

    # Earliest funding timestamp from fetched data
    if funding_rows:
        earliest_ns = funding_rows[0].timestamp_ns
    else:
        earliest_ns = 0

    # Latest common usable timestamp
    if latest_common and spot_snaps:
        # Use latest funding timestamp as the end (more conservative)
        latest_funding_ns = funding_rows[-1].timestamp_ns if funding_rows else 0
        # Spot extent may be slightly later, but funding is the constraint
        window_end_ns = latest_funding_ns
    else:
        window_end_ns = 0

    # Mechanical window: earliest available + 180-day warmup through latest common usable
    lookback_margin_ns = 180 * 86_400 * 1_000_000_000
    window_start_ns = earliest_ns + lookback_margin_ns

    print(f"  Window start : {_fmt_ns_dt(window_start_ns)}")
    print(f"  Window end   : {_fmt_ns_dt(window_end_ns)}")
    print()
    print("  Mechanical rationale:")
    print(f"    - Earliest common available data: {_fmt_ns_dt(earliest_ns)}")
    print("    - +180-day percentile warmup: 180 calendar days")
    print(f"    - Latest common available data: {_fmt_ns_dt(window_end_ns)}")
    print("    - Window = earliest_available + 180_days through latest_common_available")
    print("    - Cell counts were NOT used to choose this window.")
    print()
    print("  Reference cell counts under this window:")
    print(f"    Cells with >= 50 events  : {coverage.cells_with_ge_50_events}/60")
    print(f"    Cells with >= 100 events : {coverage.cells_with_ge_100_events}/60")
    print()

    # Write proposal JSON with all audit metadata
    proposal = {
        "status": "WINDOW_PROPOSAL_READY",
        "generated_at": datetime.now(UTC).isoformat(),
        "data_source": "Binance Vision archive (data.binance.vision)",
        "symbol_funding": "BTCUSDT",
        "symbol_spot": "BTCUSDT",
        "requested_range_start": req_start,
        "requested_range_end": req_end,
        "latest_checked_month": latest_checked,
        "available_funding_range": {
            "start": earliest_common_label,
            "end": _month_label(*latest_funding) if latest_funding else "N/A",
        },
        "available_spot_range": {
            "start": earliest_common_label,
            "end": _month_label(*latest_spot) if latest_spot else "N/A",
        },
        "latest_common_usable_month": latest_common_label,
        "missing_funding_months": len(missing_funding),
        "failed_funding_months": len(failed_funding),
        "missing_spot_months": len(missing_spot),
        "failed_spot_months": len(failed_spot),
        "funding_source_path": "/data/futures/um/monthly/fundingRate/BTCUSDT/",
        "spot_source_path": "/data/spot/monthly/klines/BTCUSDT/1h/",
        "funding_observations": coverage.total_funding_obs,
        "spot_observations": len(spot_snaps),
        "earliest_funding": _fmt_ns_dt(coverage.earliest_funding_ns),
        "latest_funding": _fmt_ns_dt(coverage.latest_funding_ns),
        "earliest_spot": _fmt_ns_dt(spot_snaps[0].timestamp_ns if spot_snaps else None),
        "latest_spot": _fmt_ns_dt(spot_snaps[-1].timestamp_ns if spot_snaps else None),
        "funding_interval_metadata": coverage.funding_interval_metadata_status,
        "cells_with_past_only_history": coverage.cells_with_past_only_history,
        "cells_with_ge_50_events": coverage.cells_with_ge_50_events,
        "cells_with_ge_100_events": coverage.cells_with_ge_100_events,
        "proposed_window_start_ns": window_start_ns,
        "proposed_window_end_ns": window_end_ns,
        "proposed_window_start": _fmt_ns_dt(window_start_ns),
        "proposed_window_end": _fmt_ns_dt(window_end_ns),
        "window_selection_rationale": (
            "Mechanical: earliest common available data + 180-day percentile warmup "
            "through latest common available data. Cell counts were NOT used to "
            "choose this window."
        ),
        "content_hashes": coverage.content_hashes,
        "total_cached_files": len(coverage.content_hashes),
        "branches_to_not_cross": [
            "Do not execute the real evaluation run until window is approved.",
            "Do not change thresholds, horizons, cost model, or gates after approval.",
        ],
    }

    proposal_path = Path(args.cache_dir) / "window_proposal.json"
    proposal_path.write_text(json.dumps(proposal, indent=2, default=str), encoding="utf-8")
    print(f"  Proposal written to: {proposal_path}")
    print()

    print("=" * 70)
    print("STATUS: WINDOW_PROPOSAL_READY")
    print("=" * 70)
    print()
    print("  The archive has been probed, data cached, coverage computed.")
    print("  The window proposal is ready for human review.")
    print()
    print("  NEXT STEP (separate task):")
    print("  - Approve or reject the proposed window.")
    print("  - After approval, execute one frozen 60-cell run.")
    print("  - Do not tune thresholds/gates/cost model after seeing results.")
    print()

    return 0


# ---------------------------------------------------------------------------
# Run mode (future)
# ---------------------------------------------------------------------------


def run_evaluation(args: argparse.Namespace) -> int:
    """
    Execute the frozen 60-cell evaluation run.

    This function is reserved for the post-approval evaluation task.
    It must not be called in this pre-run coverage task.
    """
    print("=" * 70)
    print("RUN MODE — NOT YET AVAILABLE")
    print("=" * 70)
    print()
    print("  The real evaluation run is a separate future task.")
    print("  Use --mode coverage for the current pre-run task.")
    print()
    print("  Requirements before run mode can execute:")
    print("  - Window proposal approved by human")
    print("  - Cached data verified by content hash")
    print()
    return 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Family 2 funding crowding reversal — evaluation CLI.",
    )
    p.add_argument(
        "--mode",
        default="coverage",
        choices=["coverage", "run"],
        help="'coverage' (default) fetches data and proposes window. "
        "'run' executes the frozen evaluation (future task).",
    )
    p.add_argument(
        "--cache-dir",
        default="data/funding_crowding_cache",
        help="Local directory for cached Binance Vision archives.",
    )
    p.add_argument("--start-year", type=int, default=2020, help="Start year for data fetch.")
    p.add_argument("--start-month", type=int, default=1, help="Start month for data fetch.")
    p.add_argument("--end-year", type=int, default=None, help="End year for data fetch. Defaults to current month.")
    p.add_argument("--end-month", type=int, default=None, help="End month for data fetch. Defaults to current month.")
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Default end date to current month
    if args.end_year is None or args.end_month is None:
        now = datetime.now(UTC)
        if args.end_year is None:
            args.end_year = now.year
        if args.end_month is None:
            args.end_month = now.month

    if args.mode == "coverage":
        return run_coverage(args)
    elif args.mode == "run":
        return run_evaluation(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
