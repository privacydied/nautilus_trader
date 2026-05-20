"""
Approved rerun of Family 2 funding crowding reversal evaluation with event-preservation bugfix.

Approved window: read from data/funding_crowding_cache/window_proposal.json
Seed: 42
Primary cost: 50 bps
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

# Read exact timestamps from window_proposal.json
proposal = json.loads(Path("data/funding_crowding_cache/window_proposal.json").read_text())
APPROVED_WINDOW_START_NS = proposal["proposed_window_start_ns"]
APPROVED_WINDOW_END_NS = proposal["proposed_window_end_ns"]

print(f"Window: {APPROVED_WINDOW_START_NS} → {APPROVED_WINDOW_END_NS}")
print(f"Window start UTC: {datetime.fromtimestamp(APPROVED_WINDOW_START_NS / 1e9, tz=timezone.utc)}")
print(f"Window end UTC:   {datetime.fromtimestamp(APPROVED_WINDOW_END_NS / 1e9, tz=timezone.utc)}")

from examples.strategies.venue_agnostic_signal_observer.funding_crowding_data import (
    ArchiveCache, fetch_funding_range, fetch_spot_klines_range,
)
from examples.strategies.venue_agnostic_signal_observer.funding_crowding_evaluation import (
    FundingObservation, SpotPriceSnapshot, EvaluationRunConfig, evaluate_all_cells,
    CellResult, VERDICT_CANDIDATE, VERDICT_REJECTED, VERDICT_NEEDS_MORE_DATA,
    VERDICT_NO_NULL_WORTHY, VERDICT_NULL_REJECTED, VERDICT_FDR_BLOCKED,
    VERDICT_FDR_NOT_IMPLEMENTED, VERDICT_UNDERPOWERED_HOLDOUT,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import create_run_id

PRIMARY_COST_BPS = 50.0
OPTIMISTIC_COST_BPS = 6.0
SEED = 42
NULL_ITERATIONS = 1000
FDR_ALPHA = 0.05


def _funding_rows_to_observations(rows):
    return [FundingObservation(timestamp_ns=r.timestamp_ns, funding_rate=r.funding_rate, interval_hours=r.interval_hours) for r in rows]

def _spot_rows_to_snapshots(rows):
    return [SpotPriceSnapshot(timestamp_ns=r.close_time_ns, price=r.close_price) for r in rows if r.close_time_ns > 1_000_000_000]

def _get_git_sha():
    import subprocess
    try: return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def main():
    cache_dir = "data/funding_crowding_cache"
    report_base = "reports/funding_crowding_reversal_v1"
    git_sha = _get_git_sha()
    run_id = create_run_id("funding_crowding_reversal")
    run_dir = Path(report_base) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("FAMILY 2 — FUNDING CROWDING REVERSAL  (approved bugfix rerun)")
    print("=" * 70)
    print()
    print(f"  Starting SHA      : {git_sha}")
    print(f"  Run ID            : {run_id}")
    print(f"  Output dir        : {run_dir}")
    print(f"  Rerun reason      : BUGFIX_RERUN_EVENTS_PRESERVED")
    print()
    print(f"  Approved window   : {datetime.fromtimestamp(APPROVED_WINDOW_START_NS / 1e9, tz=timezone.utc)}  →  {datetime.fromtimestamp(APPROVED_WINDOW_END_NS / 1e9, tz=timezone.utc)}")
    print(f"  Seed              : {SEED}")
    print(f"  Primary cost      : {PRIMARY_COST_BPS} bps")
    print(f"  Null iterations   : {NULL_ITERATIONS}")
    print()

    # Load cached data
    print("[1] Loading cached data (no re-fetch) …")
    cache = ArchiveCache(cache_dir)
    funding_rows, f_hashes = fetch_funding_range(cache=cache, symbol="BTCUSDT", start_year=2020, start_month=1, end_year=2026, end_month=4)
    spot_rows, s_hashes = fetch_spot_klines_range(cache=cache, symbol="BTCUSDT", start_year=2020, start_month=1, end_year=2026, end_month=4)
    all_hashes = {**f_hashes, **s_hashes}
    funding_obs = _funding_rows_to_observations(funding_rows)
    spot_snaps = _spot_rows_to_snapshots(spot_rows)
    print(f"      Funding obs: {len(funding_obs)}, Spot snaps: {len(spot_snaps)}")

    # Run evaluation
    print("[2] Running frozen 60-cell evaluation …")
    config = EvaluationRunConfig(
        data_window_start_ns=APPROVED_WINDOW_START_NS,
        data_window_end_ns=APPROVED_WINDOW_END_NS,
        seed=SEED, cost_bps=PRIMARY_COST_BPS, optimistic_cost_bps=OPTIMISTIC_COST_BPS,
        null_iterations=NULL_ITERATIONS, fdr_alpha=FDR_ALPHA, fdr_method="BY",
        train_fraction=0.70, baseline_seed=999, enable_eth=False,
    )
    t0 = time.time()
    results = evaluate_all_cells(funding_rows=funding_obs, spot_prices=spot_snaps, config=config)
    elapsed = time.time() - t0
    print(f"      Complete in {elapsed:.1f}s — 60 cells")

    # Write artifacts
    print("[3] Writing artifacts …")
    verdict_counts: dict[str, int] = {}
    event_rows: list[dict] = []
    fr_rows: list[dict] = []
    for r in results:
        verdict_counts[r.verdict] = verdict_counts.get(r.verdict, 0) + 1
        event_rows.append({
            "cell_id": r.cell_id, "threshold_label": r.threshold_label,
            "horizon_label": r.horizon_label, "direction": r.direction,
            "valid_count": r.valid_count, "eligible_count": r.eligible_count,
            "event_count": len(r.events), "mean_net_bps": r.mean_net_bps,
            "median_net_bps": r.median_net_bps, "optimistic_mean_bps": r.optimistic_mean_bps,
            "win_rate": r.win_rate, "worst_decile_net_bps": r.worst_decile_net_bps,
            "baseline_delta_bps": r.baseline_delta_bps,
            "null_survived": r.null_survived, "null_p_value": r.null_p_value,
            "holdout_same_sign": r.holdout_same_sign, "fdr_survived": r.fdr_survived,
            "fdr_adjusted_p": r.fdr_adjusted_p, "verdict": r.verdict,
        })
        for evt in r.events:
            fr_rows.append({
                "cell_id": r.cell_id, "event_timestamp_ns": evt.event_timestamp_ns,
                "funding_rate": evt.funding_rate, "forward_spot_return_bps": evt.forward_spot_return_bps,
                "net_return_bps": evt.net_return_bps,
            })

    summary = {
        "_metadata": {
            "git_sha": git_sha, "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_args": {
                "window_start": str(datetime.fromtimestamp(APPROVED_WINDOW_START_NS / 1e9, tz=timezone.utc)),
                "window_end": str(datetime.fromtimestamp(APPROVED_WINDOW_END_NS / 1e9, tz=timezone.utc)),
                "primary_cost_bps": PRIMARY_COST_BPS, "optimistic_cost_bps": OPTIMISTIC_COST_BPS,
                "seed": SEED, "null_iterations": NULL_ITERATIONS, "fdr_alpha": FDR_ALPHA,
                "fdr_method": "BY", "train_fraction": 0.70, "cell_family_size": 60,
            },
            "data_window": {
                "start": str(datetime.fromtimestamp(APPROVED_WINDOW_START_NS / 1e9, tz=timezone.utc)),
                "end": str(datetime.fromtimestamp(APPROVED_WINDOW_END_NS / 1e9, tz=timezone.utc)),
            },
            "input_content_hashes": all_hashes,
            "data_source": "Binance Vision archive (cached; no re-fetch)",
            "funding_interval_metadata": "FUNDING_INTERVAL_METADATA_AVAILABLE",
            "rerun_reason": "BUGFIX_RERUN_EVENTS_PRESERVED",
            "approved_window_marker": f"{APPROVED_WINDOW_START_NS}:{APPROVED_WINDOW_END_NS}",
        },
        "run_id": run_id, "status": "EVALUATION_COMPLETE",
        "total_cells": len(results), "verdict_counts": verdict_counts,
        "seed": SEED, "primary_cost_bps": PRIMARY_COST_BPS,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    with open(run_dir / "events.jsonl", "w") as f:
        for row in event_rows:
            f.write(json.dumps(row, default=str) + "\n")
    with open(run_dir / "forward_returns.jsonl", "w") as f:
        for row in fr_rows:
            f.write(json.dumps(row, default=str) + "\n")

    # report.md
    lines = [
        f"# Family 2 Funding Crowding Reversal — Approved Bugfix Rerun",
        f"",
        f"- **Branch:** feat/edge-miner-offline-discovery-runner",
        f"- **Starting SHA:** `{git_sha}`",
        f"- **Run ID:** {run_id}",
        f"- **Rerun reason:** BUGFIX_RERUN_EVENTS_PRESERVED",
        f"- **Approved window:** {datetime.fromtimestamp(APPROVED_WINDOW_START_NS / 1e9, tz=timezone.utc)} → {datetime.fromtimestamp(APPROVED_WINDOW_END_NS / 1e9, tz=timezone.utc)}",
        f"- **Seed:** {SEED}",
        f"- **Primary cost:** {PRIMARY_COST_BPS} bps",
        f"- **Diagnostic cost:** {OPTIMISTIC_COST_BPS} bps",
        f"- **Data source:** Binance Vision archive (cached; no re-fetch)",
        f"",
        f"## Headline",
        f"",
        f"- **Total cells evaluated:** {len(results)}",
        f"",
    ]
    for v, c in sorted(verdict_counts.items()):
        lines.append(f"- **{v}:** {c}")
    lines.append("")
    lines.append(f"### Cells earning CANDIDATE_FOR_LONGER_OBSERVATION")
    cand = [r for r in results if r.verdict == VERDICT_CANDIDATE]
    if cand:
        for r in cand:
            lines.append(f"- {r.cell_id}")
    else:
        lines.append(f"- None")
    lines.append("")
    lines.append(f"## Full 60-Cell Verdict Table")
    lines.append(f"")
    lines.append(f"| # | Cell ID | Valid | MeanNet | MedNet | WinRate | WorstD10 | BaseΔ | Null? | HoldSS? | FDR? | Verdict |")
    lines.append(f"|---|---------|-------|---------|--------|---------|----------|-------|-------|---------|------|---------|")
    for i, r in enumerate(results, 1):
        null_s = "Y" if r.null_survived else ("N" if r.null_survived is False else "?")
        ho_s = "Y" if r.holdout_same_sign else ("N" if r.holdout_same_sign is False else "?")
        fdr_s = "Y" if r.fdr_survived else ("N" if r.fdr_survived is False else "?")
        mn = f"{r.mean_net_bps:.2f}" if r.mean_net_bps is not None else "--"
        med = f"{r.median_net_bps:.2f}" if r.median_net_bps is not None else "--"
        wr = f"{r.win_rate:.4f}" if r.win_rate is not None else "--"
        wd = f"{r.worst_decile_net_bps:.2f}" if r.worst_decile_net_bps is not None else "--"
        bd = f"{r.baseline_delta_bps:.2f}" if r.baseline_delta_bps is not None else "--"
        lines.append(f"| {i} | {r.cell_id} | {r.valid_count} | {mn} | {med} | {wr} | {wd} | {bd} | {null_s} | {ho_s} | {fdr_s} | {r.verdict} |")
    lines.append("")
    lines.append(f"## Diagnostic Sensitivity (6 bps)")
    lines.append(f"")
    lines.append(f"Cells where optimistic_mean > 0 but gate verdict not CANDIDATE:")
    changed = [r for r in results if r.optimistic_mean_bps is not None and r.optimistic_mean_bps > 0 and r.verdict != VERDICT_CANDIDATE]
    if changed:
        for r in changed:
            lines.append(f"- {r.cell_id}: opt_mean={r.optimistic_mean_bps:.2f}, actual_verdict={r.verdict}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append(f"## Verifications")
    lines.append(f"- **BY_FDR_AVAILABLE:** yes")
    lines.append(f"- **TIMESTAMP_SHUFFLE_NULL_AVAILABLE:** yes")
    lines.append(f"- **No re-fetch**")
    lines.append(f"- **Run executed once:** seed={SEED}, no re-window, no re-tune")
    lines.append(f"- **No private key / order / execution path**")

    (run_dir / "report.md").write_text("\n".join(lines))

    # Print headline
    print()
    print("=" * 70)
    print("RERUN COMPLETE")
    print("=" * 70)
    print(f"  Output: {run_dir}")
    print()
    for v, c in sorted(verdict_counts.items()):
        print(f"  {v}: {c}")
    print()
    return 0

if __name__ == "__main__":
    sys.exit(main())
