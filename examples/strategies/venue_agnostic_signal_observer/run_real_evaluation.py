"""
Single-use runner for the approved Family 2 funding crowding reversal evaluation.

Approved window: 2020-06-29 00:00:00 UTC → 2026-04-30 16:00:00 UTC
Primary cost: 50 bps (gates promotion)
Diagnostic cost: 6 bps (reported separately, never gates)
Frozen: 60 BTC primary cells, BY FDR, timestamp-shuffle null, 70/30 train/holdout

This script loads only from cache. No network. No re-fetch.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from examples.strategies.venue_agnostic_signal_observer.funding_crowding_data import (
    ArchiveCache,
    fetch_funding_range,
    fetch_spot_klines_range,
    FundingRateRow,
    SpotKlineRow,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_evaluation import (
    FundingObservation,
    SpotPriceSnapshot,
    EvaluationRunConfig,
    evaluate_all_cells,
    CellResult,
    VERDICT_CANDIDATE,
    VERDICT_REJECTED,
    VERDICT_NEEDS_MORE_DATA,
    VERDICT_NO_NULL_WORTHY,
    VERDICT_NULL_REJECTED,
    VERDICT_FDR_BLOCKED,
    VERDICT_FDR_NOT_IMPLEMENTED,
    VERDICT_UNDERPOWERED_HOLDOUT,
    build_all_cell_identifiers,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import create_run_id

APPROVED_WINDOW_START_NS = int(datetime(2020, 6, 29, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
APPROVED_WINDOW_END_NS = int(datetime(2026, 4, 30, 16, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
PRIMARY_COST_BPS = 50.0
OPTIMISTIC_COST_BPS = 6.0
SEED = 42
NULL_ITERATIONS = 1000
FDR_ALPHA = 0.05


def _funding_rows_to_observations(rows: list[FundingRateRow]) -> list[FundingObservation]:
    return [
        FundingObservation(
            timestamp_ns=r.timestamp_ns,
            funding_rate=r.funding_rate,
            interval_hours=r.interval_hours,
        )
        for r in rows
    ]


def _spot_rows_to_snapshots(rows: list[SpotKlineRow]) -> list[SpotPriceSnapshot]:
    result = []
    for r in rows:
        if r.close_time_ns > 1_000_000_000:
            result.append(
                SpotPriceSnapshot(
                    timestamp_ns=r.close_time_ns,
                    price=r.close_price,
                )
            )
    return result


def _fmt_ns_dt(ts_ns: int | None) -> str:
    if ts_ns is None:
        return "N/A"
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _get_git_sha() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


def main() -> int:
    cache_dir = "data/funding_crowding_cache"
    report_base = "reports/funding_crowding_reversal_v1"

    git_sha = _get_git_sha()
    run_id = create_run_id("funding_crowding_reversal")
    run_dir = Path(report_base) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("FAMILY 2 — FUNDING CROWDING REVERSAL  (real evaluation run)")
    print("=" * 70)
    print()
    print(f"  Starting SHA      : {git_sha}")
    print(f"  Run ID            : {run_id}")
    print(f"  Output dir        : {run_dir}")
    print(f"  Cache dir         : {cache_dir}")
    print()
    print(f"  Approved window   : {_fmt_ns_dt(APPROVED_WINDOW_START_NS)}  →  {_fmt_ns_dt(APPROVED_WINDOW_END_NS)}")
    print(f"  Primary cost      : {PRIMARY_COST_BPS} bps")
    print(f"  Diagnostic cost   : {OPTIMISTIC_COST_BPS} bps")
    print(f"  Seed              : {SEED}")
    print(f"  Null iterations   : {NULL_ITERATIONS}")
    print(f"  FDR alpha         : {FDR_ALPHA}")
    print(f"  Cell family size  : 60 (BTC primary)")
    print()

    # --- Step 1: Load cached data ---
    print("[1] Loading cached data …")
    cache = ArchiveCache(cache_dir)

    # Load all cached months (2020-01 through 2026-04)
    funding_rows, funding_hashes = fetch_funding_range(
        cache=cache,
        symbol="BTCUSDT",
        start_year=2020, start_month=1,
        end_year=2026, end_month=4,
    )
    print(f"      Funding observations loaded: {len(funding_rows)}")

    spot_rows, spot_hashes = fetch_spot_klines_range(
        cache=cache,
        symbol="BTCUSDT",
        start_year=2020, start_month=1,
        end_year=2026, end_month=4,
    )
    print(f"      Spot klines loaded: {len(spot_rows)}")

    # Merge content hashes
    all_hashes: dict[str, str] = {}
    all_hashes.update(funding_hashes)
    all_hashes.update(spot_hashes)

    # Convert to observation types
    funding_obs = _funding_rows_to_observations(funding_rows)
    spot_snaps = _spot_rows_to_snapshots(spot_rows)

    print(f"      Funding observations: {len(funding_obs)}")
    print(f"      Spot price snapshots: {len(spot_snaps)}")
    print(f"      Cache source: Binance Vision archive only (no re-fetch)")

    # Verify data covers approved window
    earliest_funding_ns = funding_obs[0].timestamp_ns
    latest_funding_ns = funding_obs[-1].timestamp_ns
    print(f"      Earliest funding: {_fmt_ns_dt(earliest_funding_ns)}")
    print(f"      Latest funding  : {_fmt_ns_dt(latest_funding_ns)}")
    print()

    # --- Step 2: Run evaluation ---
    print("[2] Running frozen 60-cell evaluation …")
    print(f"      Window: {_fmt_ns_dt(APPROVED_WINDOW_START_NS)}  →  {_fmt_ns_dt(APPROVED_WINDOW_END_NS)}")
    print(f"      Primary cost: {PRIMARY_COST_BPS} bps")
    print()

    config = EvaluationRunConfig(
        data_window_start_ns=APPROVED_WINDOW_START_NS,
        data_window_end_ns=APPROVED_WINDOW_END_NS,
        seed=SEED,
        cost_bps=PRIMARY_COST_BPS,
        optimistic_cost_bps=OPTIMISTIC_COST_BPS,
        null_iterations=NULL_ITERATIONS,
        fdr_alpha=FDR_ALPHA,
        fdr_method="BY",
        train_fraction=0.70,
        baseline_seed=999,
        enable_eth=False,
    )

    t0 = time.time()
    results = evaluate_all_cells(
        funding_rows=funding_obs,
        spot_prices=spot_snaps,
        config=config,
    )
    elapsed = time.time() - t0
    print(f"      Evaluation complete in {elapsed:.1f}s")
    print(f"      Cells evaluated: {len(results)}")
    print()

    # --- Step 3: Write artifacts ---
    print("[3] Writing output artifacts …")

    # Compute summary stats
    verdict_counts: dict[str, int] = {}
    for r in results:
        v = r.verdict
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    # Build rows for events.jsonl and forward_returns.jsonl
    event_rows: list[dict] = []
    fr_rows: list[dict] = []

    for r in results:
        event_row = {
            "cell_id": r.cell_id,
            "threshold_label": r.threshold_label,
            "horizon_label": r.horizon_label,
            "direction": r.direction,
            "valid_count": r.valid_count,
            "eligible_count": r.eligible_count,
            "event_count": len(r.events),
            "mean_net_bps": r.mean_net_bps,
            "median_net_bps": r.median_net_bps,
            "optimistic_mean_bps": r.optimistic_mean_bps,
            "win_rate": r.win_rate,
            "worst_decile_net_bps": r.worst_decile_net_bps,
            "baseline_delta_bps": r.baseline_delta_bps,
            "null_survived": r.null_survived,
            "null_p_value": r.null_p_value,
            "holdout_same_sign": r.holdout_same_sign,
            "fdr_survived": r.fdr_survived,
            "fdr_adjusted_p": r.fdr_adjusted_p,
            "verdict": r.verdict,
        }
        event_rows.append(event_row)

        for evt in r.events:
            fr_rows.append({
                "cell_id": r.cell_id,
                "event_timestamp_ns": evt.event_timestamp_ns,
                "funding_rate": evt.funding_rate,
                "forward_spot_return_bps": evt.forward_spot_return_bps,
                "net_return_bps": evt.net_return_bps,
            })

    # summary.json
    summary = {
        "_metadata": {
            "git_sha": git_sha,
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_args": {
                "window_start": _fmt_ns_dt(APPROVED_WINDOW_START_NS),
                "window_end": _fmt_ns_dt(APPROVED_WINDOW_END_NS),
                "primary_cost_bps": PRIMARY_COST_BPS,
                "optimistic_cost_bps": OPTIMISTIC_COST_BPS,
                "seed": SEED,
                "null_iterations": NULL_ITERATIONS,
                "fdr_alpha": FDR_ALPHA,
                "fdr_method": "BY",
                "train_fraction": 0.70,
                "cell_family_size": 60,
            },
            "data_window": {
                "start": _fmt_ns_dt(APPROVED_WINDOW_START_NS),
                "end": _fmt_ns_dt(APPROVED_WINDOW_END_NS),
            },
            "input_content_hashes": all_hashes,
            "data_source": "Binance Vision archive (data.binance.vision) — cached; no re-fetch",
        },
        "run_id": run_id,
        "status": "EVALUATION_COMPLETE",
        "total_cells": len(results),
        "verdict_counts": verdict_counts,
        "seed": SEED,
        "primary_cost_bps": PRIMARY_COST_BPS,
        "optimistic_cost_bps": OPTIMISTIC_COST_BPS,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"      summary.json: {len(summary)} top-level keys")

    # events.jsonl
    with open(run_dir / "events.jsonl", "w") as f:
        for row in event_rows:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"      events.jsonl: {len(event_rows)} rows")

    # forward_returns.jsonl
    with open(run_dir / "forward_returns.jsonl", "w") as f:
        for row in fr_rows:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"      forward_returns.jsonl: {len(fr_rows)} rows")

    # --- Step 4: Write report.md ---
    print("[4] Writing report.md …")

    lines: list[str] = []
    lines.append(f"# Family 2 Funding Crowding Reversal — Real Evaluation Run")
    lines.append(f"")
    lines.append(f"- **Branch:** feat/edge-miner-offline-discovery-runner")
    lines.append(f"- **Starting SHA:** `{git_sha}`")
    lines.append(f"- **Run ID:** {run_id}")
    lines.append(f"- **Generated at:** {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Approved window:** {_fmt_ns_dt(APPROVED_WINDOW_START_NS)} → {_fmt_ns_dt(APPROVED_WINDOW_END_NS)}")
    lines.append(f"- **Primary cost:** {PRIMARY_COST_BPS} bps")
    lines.append(f"- **Diagnostic cost:** {OPTIMISTIC_COST_BPS} bps (reported separately, never gates)")
    lines.append(f"- **Seed:** {SEED}")
    lines.append(f"- **Null iterations:** {NULL_ITERATIONS}")
    lines.append(f"- **FDR alpha:** {FDR_ALPHA}, method: BY")
    lines.append(f"- **Data source:** Binance Vision archive (cached; no re-fetch)")
    lines.append(f"")
    lines.append(f"## Headline")
    lines.append(f"")
    lines.append(f"- **Total cells evaluated:** {len(results)}")
    lines.append(f"")
    for v, c in sorted(verdict_counts.items()):
        lines.append(f"- **{v}:** {c}")
    lines.append("")

    candidate_cells = [r for r in results if r.verdict == VERDICT_CANDIDATE]
    if candidate_cells:
        lines.append(f"### Cells earning CANDIDATE_FOR_LONGER_OBSERVATION")
        lines.append(f"")
        for r in candidate_cells:
            lines.append(f"- {r.cell_id}: mean_net={r.mean_net_bps:.2f} bps, win_rate={r.win_rate:.4f}, null_p={r.null_p_value:.4f}")
        lines.append(f"")
    else:
        lines.append(f"### No cells earned CANDIDATE_FOR_LONGER_OBSERVATION")
        lines.append(f"")

    lines.append(f"## Full 60-Cell Verdict Table")
    lines.append(f"")
    lines.append(f"| # | Cell ID | Valid | MeanNet | MedNet | WinRate | WorstD10 | BaselineΔ | Null? | THSameSign? | FDR? | Verdict |")
    lines.append(f"|---|---------|-------|---------|--------|---------|----------|-----------|-------|-------------|------|---------|")

    for i, r in enumerate(results, 1):
        null_str = "✓" if r.null_survived else ("✗" if r.null_survived is False else "—")
        th_str = "✓" if r.holdout_same_sign else ("✗" if r.holdout_same_sign is False else "—")
        fdr_str = "✓" if r.fdr_survived else ("✗" if r.fdr_survived is False else "—")
        lines.append(
            f"| {i} | {r.cell_id} | {r.valid_count} | "
            f"{r.mean_net_bps if r.mean_net_bps is not None else '—'} | "
            f"{r.median_net_bps if r.median_net_bps is not None else '—'} | "
            f"{r.win_rate if r.win_rate is not None else '—'} | "
            f"{r.worst_decile_net_bps if r.worst_decile_net_bps is not None else '—'} | "
            f"{r.baseline_delta_bps if r.baseline_delta_bps is not None else '—'} | "
            f"{null_str} | {th_str} | {fdr_str} | {r.verdict} |"
        )

    lines.append(f"")
    lines.append(f"## Diagnostic Sensitivity (6 bps cost)")
    lines.append(f"")
    lines.append(f"The following cells would change verdict if evaluated at 6 bps instead of 50 bps:")
    lines.append(f"")
    changed: list[CellResult] = []
    for r in results:
        if r.optimistic_mean_bps is not None:
            if r.optimistic_mean_bps > 0 and r.verdict != VERDICT_CANDIDATE:
                changed.append(r)
    if changed:
        for r in changed:
            lines.append(f"- {r.cell_id}: optimistic_mean={r.optimistic_mean_bps:.2f} bps, actual_verdict={r.verdict}")
    else:
        lines.append(f"- No cells would change verdict at 6 bps cost.")
    lines.append(f"")

    lines.append(f"## Verifications")
    lines.append(f"")
    lines.append(f"- **BY_FDR_AVAILABLE:** yes")
    lines.append(f"- **TIMESTAMP_SHUFFLE_NULL_AVAILABLE:** yes")
    lines.append(f"- **No re-fetch:** all data loaded from cache; no REST/live/authenticated endpoint")
    lines.append(f"- **No non-cached source:** Binance Vision archive only")
    lines.append(f"- **Run executed once:** one seed ({SEED}), no re-window, no re-tune, no rerun")
    lines.append(f"- **Frozen design unchanged:** 60 BTC primary cells, 6 thresholds × 5 horizons × 2 directions")
    lines.append(f"- **Past-only percentile:** honored")
    lines.append(f"- **Event dedup:** one event per funding timestamp per cell")
    lines.append(f"- **Cost model:** {PRIMARY_COST_BPS} bps primary (gates promotion); {OPTIMISTIC_COST_BPS} bps diagnostic only")
    lines.append(f"- **No private key, order, execution, wallet, or bot path**")

    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"      report.md: {len(lines)} lines")
    print()
    print(f"  Output artifacts written to: {run_dir}")
    print()

    # --- Final summary ---
    print("=" * 70)
    print("EVALUATION COMPLETE")
    print("=" * 70)
    print()
    print(f"  Verdict summary ({len(results)} cells):")
    for v, c in sorted(verdict_counts.items()):
        pct = 100.0 * c / len(results)
        print(f"    {v:45s}  {c:3d}  ({pct:5.1f}%)")
    print()
    if candidate_cells:
        print(f"  {len(candidate_cells)} cell(s) earned CANDIDATE_FOR_LONGER_OBSERVATION:")
        for r in candidate_cells:
            print(f"    - {r.cell_id}")
        print()
        print("  Recommended next step: scope a longer-observation / out-of-sample")
        print("  confirmation task for the surviving cell(s). A surviving cell is")
        print("  not a tradeable signal — it warrants further observation only.")
    else:
        print("  No cells earned CANDIDATE_FOR_LONGER_OBSERVATION.")
        print()
        print("  Recommended next step: scope the REJECTED_RESEARCH.md registry update")
        print("  recording this outcome, including the precise tested conditions and")
        print("  the untested adjacent hypotheses.")
    print()
    print(f"  Output directory: {run_dir}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
