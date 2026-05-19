#!/usr/bin/env python3
"""CLI runner for cross-asset beta-lag archive v0 study.

Study: cross_asset_beta_lag_archive_v0

Usage:
    python3 -m examples.strategies.venue_agnostic_signal_observer.run_cross_asset_beta_lag_archive
        --precommitment <path>
        --out <dir>
        [--seed 42]
        [--null-iterations 1000]

Public data observer only. No orders. No execution. No auth.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure we can import sibling modules
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(_THIS_DIR))

from .tick_models import TickForwardReturn, TradeTickLite

from .binance_vision_archive import (
    download_daily_agg_trades,
    scan_archive_availability,
    compute_common_calendar,
    parse_agg_trade_csv,
    _iter_date_range,
    _sha256_bytes,
    _sha256_file,
)
from .cross_asset_beta_lag_archive import (
    ALL_SYMBOLS,
    SOURCE_SYMBOLS,
    TARGET_SYMBOLS,
    CALENDAR_START,
    CALENDAR_END,
    MIN_CALENDAR_DAYS,
    MIN_INDEPENDENT_WINDOWS,
    STRESS_RULES,
    STRESS_DEDUP_COOLDOWN_NS,
    HORIZONS_MS,
    STRESS_WINDOW_SECONDS,
    DIRECTIONS,
    FAMILY_SIZE,
    VENUE,
    TOTAL_COST_BPS,
    SEED,
    NULL_ITERATIONS,
    FDR_ALPHA,
    TRAIN_FRAC,
    MIN_EVENTS_PER_CELL,
    MIN_EVENTS_HOLDOUT,
    WIN_RATE_THRESHOLD,
    WORST_DECILE_THRESHOLD,
    BASELINE_DELTA_BPS,
    NULL_ALPHA,

    StressLabel,
    CoverageInterval,
    generate_stress_labels,
    deduplicate_labels,
    assign_independent_windows,
    check_target_coverage,
    compute_forward_returns_for_stress,
    compute_cell_stats,
    cell_group_key,
    all_cell_keys,
    generate_baseline_events,
    run_null_test,
    apply_by_fdr,
    reconcile_event_vector,
    write_report,
    _get_git_sha,
    _now_utc_iso,
    _sha256_json,
    MS_TO_NS,
    ENTRY_DELAY_NS,
)

from .run_artifacts import (
    create_run_id,
    create_run_dir,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    safe_output_dir,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-asset beta-lag archive v0 study runner"
    )
    parser.add_argument(
        "--precommitment", required=True,
        help="Path to precommitment JSON file",
    )
    parser.add_argument(
        "--out", required=True,
        help="Output directory for reports",
    )
    parser.add_argument(
        "--seed", type=int, default=SEED,
        help="Random seed (default: 42)",
    )
    parser.add_argument(
        "--null-iterations", type=int, default=NULL_ITERATIONS,
        help="Null test iterations (default: 1000)",
    )
    args = parser.parse_args()

    # Print start info
    git_sha = _get_git_sha()
    print(f"=== Cross-Asset Beta-Lag Archive v0 ===")
    print(f"Git SHA: {git_sha}")
    print(f"Start: {_now_utc_iso()}")
    print(f"Precommitment: {args.precommitment}")
    print(f"Output: {args.out}")
    print(f"Seed: {args.seed}, null iterations: {args.null_iterations}")
    print(f"Family size: {FAMILY_SIZE}")
    print(f"Source: {SOURCE_SYMBOLS}")
    print(f"Targets: {TARGET_SYMBOLS}")
    print(f"Calendar: {CALENDAR_START} to {CALENDAR_END}")
    print(f"========================================\n")

    run_id = create_run_id("cross_asset_beta_lag_archive")
    output_dir = Path(args.out) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 0: Validate precommitment
    print("[Phase 0] Validating precommitment...")
    precommitment_path = Path(args.precommitment)
    if not precommitment_path.exists():
        print(f"ERROR: Precommitment not found: {args.precommitment}")
        _write_early_stop(output_dir, run_id, "PRECOMMITMENT_NOT_FOUND", git_sha)
        return

    precommitment_data = json.loads(precommitment_path.read_text("utf-8"))
    precommitment_sha = _sha256_json(precommitment_data)
    print(f"  Precommitment hash: {precommitment_sha}")

    # Phase 1: Archive availability scan
    print("\n[Phase 1] Scanning archive availability...")
    availability = scan_archive_availability(
        ALL_SYMBOLS,
        CALENDAR_START,
        CALENDAR_END,
        source="aggTrades",
    )

    # Compute common calendar
    common_start, common_end, common_days = compute_common_calendar(availability)
    print(f"  Common calendar: {common_start} to {common_end} ({common_days} days)")

    # Check minimum calendar
    if common_days < MIN_CALENDAR_DAYS:
        print(f"  INSUFFICIENT: {common_days} < {MIN_CALENDAR_DAYS} minimum calendar days")
        _write_availability_report(output_dir, run_id, git_sha, precommitment_sha,
                                   availability, common_start, common_end, common_days)
        verdict = "NEEDS_MORE_DATA_ARCHIVE_AVAILABILITY"
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       early_stop="archive_availability_insufficient")
        print(f"\n=== VERDICT: {verdict} ===")
        return

    # Estimate data volume
    date_list = _iter_date_range(common_start or CALENDAR_START, common_end or CALENDAR_END)
    estimated_files = len(ALL_SYMBOLS) * len(date_list)
    estimated_bytes_mb = estimated_files * 15  # rough ~15MB per aggTrade zip for active pairs
    print(f"  Estimated files: {estimated_files} ({estimated_bytes_mb} MB)")

    if estimated_bytes_mb > 5000:
        print(f"  ARCHIVE_DATA_VOLUME_TOO_LARGE_FOR_ONE_PROMPT: ~{estimated_bytes_mb} MB")
        _write_availability_report(output_dir, run_id, git_sha, precommitment_sha,
                                   availability, common_start, common_end, common_days)
        verdict = "NEEDS_MORE_DATA_ARCHIVE_AVAILABILITY"
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       early_stop=f"archive_data_volume_too_large_{estimated_bytes_mb}mb")
        print(f"\n=== VERDICT: {verdict} ===")
        print(f"Data volume ~{estimated_bytes_mb} MB exceeds single-run limit.")
        print("Use a narrow date window and re-run with --date-start --date-end.")
        return

    # Phase 2: Download aggTrade data for all symbols
    print(f"\n[Phase 2] Downloading archive data for {len(ALL_SYMBOLS)} symbols...")
    all_ticks: Dict[str, List[TradeTickLite]] = {}
    file_manifest: List[Dict[str, Any]] = []
    total_files = 0
    failed_files = 0

    for sym in ALL_SYMBOLS:
        print(f"  Downloading {sym}...")
        sym_ticks: List[TradeTickLite] = []
        for d in date_list:
            data, sha = download_daily_agg_trades(sym, d, cache=True)
            if data is None:
                failed_files += 1
                file_manifest.append({
                    "symbol": sym, "date": d, "source": "aggTrades",
                    "status": "not_found", "sha256": None, "size_bytes": None, "row_count": 0,
                })
                continue

            ticks = parse_agg_trade_csv(data, sym, venue=VENUE)
            sym_ticks.extend(ticks)

            file_manifest.append({
                "symbol": sym, "date": d, "source": "aggTrades",
                "status": "downloaded", "sha256": sha,
                "size_bytes": len(data), "row_count": len(ticks),
            })
            total_files += 1

        all_ticks[sym] = sym_ticks
        print(f"    {len(sym_ticks)} ticks for {sym}")

    print(f"  Total files: {total_files}, failed: {failed_files}")
    print(f"  Total ticks: {sum(len(t) for t in all_ticks.values())}")

    # Check per-symbol coverage
    missing_symbols = [sym for sym in ALL_SYMBOLS if not all_ticks.get(sym)]
    per_sym_coverage: Dict[str, Dict[str, Any]] = {}
    for sym in ALL_SYMBOLS:
        ticks = all_ticks.get(sym, [])
        per_sym_coverage[sym] = {
            "tick_count": len(ticks),
            "first_ts": ticks[0].ts_event if ticks else None,
            "last_ts": ticks[-1].ts_event if ticks else None,
        }

    # Phase 3: Generate stress labels from source ticks
    print(f"\n[Phase 3] Generating stress labels...")
    all_labels: List[StressLabel] = []

    for src_sym in SOURCE_SYMBOLS:
        source_ticks = all_ticks.get(src_sym, [])
        if not source_ticks:
            print(f"  WARNING: No ticks for {src_sym}, skipping")
            continue

        for rule_name, lookback_sec, threshold in STRESS_RULES:
            labels = generate_stress_labels(
                source_ticks, src_sym,
                lookback_seconds=lookback_sec,
                threshold_bps=threshold,
            )
            all_labels.extend(labels)
            print(f"  {src_sym} {rule_name}: {len(labels)} raw labels")

    if not all_labels:
        print("  NO STRESS LABELS GENERATED")
        _write_data_report(output_dir, run_id, git_sha, precommitment_sha,
                           availability, file_manifest, per_sym_coverage, missing_symbols)
        verdict = "NEEDS_MORE_DATA_ARCHIVE_NO_STRESS_LABELS"
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       early_stop="zero_stress_labels")
        print(f"\n=== VERDICT: {verdict} ===")
        return

    # Dedup labels
    deduped = deduplicate_labels(all_labels)
    print(f"  After dedup: {len(deduped)} labels")

    # Assign independent windows
    windowed = assign_independent_windows(deduped)
    independent_window_ids = list(set(l.independent_window_id for l in windowed))
    print(f"  Independent windows: {len(independent_window_ids)}")

    if len(independent_window_ids) < MIN_INDEPENDENT_WINDOWS:
        print(f"  INSUFFICIENT: {len(independent_window_ids)} < {MIN_INDEPENDENT_WINDOWS}")
        _write_data_report(output_dir, run_id, git_sha, precommitment_sha,
                           availability, file_manifest, per_sym_coverage, missing_symbols)
        _write_stress_label_report(output_dir, windowed, independent_window_ids)
        verdict = "NEEDS_MORE_DATA_ARCHIVE_STRESS_WINDOWS"
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       early_stop=f"insufficient_windows_{len(independent_window_ids)}_lt_{MIN_INDEPENDENT_WINDOWS}")
        print(f"\n=== VERDICT: {verdict} ===")
        return

    # Phase 4: Coverage check per label
    print(f"\n[Phase 4] Checking target coverage...")
    usable_labels: List[StressLabel] = []
    coverage_summary: Dict[str, Any] = {
        "per_target": {},
        "total_labels": len(windowed),
        "usable_labels": 0,
        "all_target_windows": 0,
    }

    for sym in TARGET_SYMBOLS:
        coverage_summary["per_target"][sym] = {
            "available_count": 0,
            "total_checked": len(windowed),
        }

    for lbl in windowed:
        target_ticks_dict = {sym: all_ticks.get(sym, []) for sym in TARGET_SYMBOLS}

        # Use max horizon for coverage
        max_horizon_ns = max(HORIZONS_MS) * MS_TO_NS
        all_covered, coverage_intervals = check_target_coverage(
            target_ticks_dict,
            lbl.stress_end_ns + ENTRY_DELAY_NS,
            max_horizon_ns,
        )

        for sym, ci in coverage_intervals.items():
            if ci.has_coverage:
                coverage_summary["per_target"][sym]["available_count"] += 1

        if all_covered:
            usable_labels.append(lbl)

    coverage_summary["usable_labels"] = len(usable_labels)
    all_target_windows = len(set(l.independent_window_id for l in usable_labels))
    coverage_summary["all_target_windows"] = all_target_windows

    print(f"  Usable labels (all targets): {len(usable_labels)}")
    print(f"  All-target windows: {all_target_windows}")

    if all_target_windows < MIN_INDEPENDENT_WINDOWS:
        print(f"  INSUFFICIENT: {all_target_windows} < {MIN_INDEPENDENT_WINDOWS}")
        _write_data_report(output_dir, run_id, git_sha, precommitment_sha,
                           availability, file_manifest, per_sym_coverage, missing_symbols)
        _write_stress_label_report(output_dir, windowed, independent_window_ids)
        verdict = "NEEDS_MORE_DATA_ARCHIVE_STRESS_WINDOWS"
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       early_stop=f"insufficient_all_target_windows_{all_target_windows}_lt_{MIN_INDEPENDENT_WINDOWS}")
        print(f"\n=== VERDICT: {verdict} ===")
        return

    # Phase 5: Compute forward returns
    print(f"\n[Phase 5] Computing forward returns...")
    forward_rows: List[Dict[str, Any]] = []
    signal_rows: List[Dict[str, Any]] = []

    for lbl in usable_labels:
        for tgt_sym in TARGET_SYMBOLS:
            target_ticks = all_ticks.get(tgt_sym, [])
            if not target_ticks:
                continue

            frs = compute_forward_returns_for_stress(
                lbl, target_ticks, tgt_sym, HORIZONS_MS,
            )

            for fr in frs:
                group_key = cell_group_key(
                    lbl.source_symbol, tgt_sym, lbl.stress_window_seconds,
                    lbl.direction, fr.horizon_ms,
                )

                row = {
                    "stress_event_id": lbl.label_id,
                    "independent_window_id": lbl.independent_window_id,
                    "source_symbol": lbl.source_symbol,
                    "target_symbol": tgt_sym,
                    "direction": lbl.direction,
                    "stress_window_seconds": lbl.stress_window_seconds,
                    "source_move_bps": lbl.source_move_bps,
                    "signal_ts": fr.signal_ts,
                    "entry_ts": fr.signal_ts,  # same as signal_ts in our model
                    "horizon_ms": fr.horizon_ms,
                    "entry_price": fr.entry_reference_price,
                    "forward_price": fr.forward_price,
                    "raw_return_bps": fr.raw_return_bps,
                    "direction_adjusted_return_bps": fr.direction_adjusted_return_bps,
                    "cost_bps": TOTAL_COST_BPS,
                    "net_bps": fr.net_return_bps,
                    "valid": fr.valid,
                    "invalid_reason": fr.rejection_reason,
                    "group_key": group_key,
                    "train_or_holdout": "",
                }
                forward_rows.append(row)

    print(f"  Forward return rows: {len(forward_rows)}")

    # Split train/holdout by stress label timestamp
    sorted_labels = sorted(usable_labels, key=lambda x: x.stress_end_ns)
    split_idx = int(len(sorted_labels) * TRAIN_FRAC)
    train_labels = set(l.label_id for l in sorted_labels[:split_idx])
    holdout_labels = set(l.label_id for l in sorted_labels[split_idx:])

    for row in forward_rows:
        if row["stress_event_id"] in train_labels:
            row["train_or_holdout"] = "train"
        else:
            row["train_or_holdout"] = "holdout"

    # Phase 6: Cell evaluation
    print(f"\n[Phase 6] Cell evaluation...")
    cell_results: Dict[str, Dict[str, Any]] = {}
    all_cell_keys = all_cell_keys()

    for gk in all_cell_keys:
        cell_events = [r for r in forward_rows if r["group_key"] == gk and r["valid"]]
        train_events = [r for r in cell_events if r["train_or_holdout"] == "train"]
        holdout_events = [r for r in cell_events if r["train_or_holdout"] == "holdout"]

        # Convert to TickForwardReturn for stats
        fr_objects: List[TickForwardReturn] = []
        for r in cell_events:
            fr = TickForwardReturn(
                signal_id=r["stress_event_id"],
                signal_ts=r["signal_ts"],
                target_venue=VENUE,
                target_symbol=r["target_symbol"],
                horizon_ms=r["horizon_ms"],
                entry_reference_price=r["entry_price"],
                forward_price=r["forward_price"],
                raw_return_bps=r["raw_return_bps"],
                direction_adjusted_return_bps=r["direction_adjusted_return_bps"],
                fee_bps=TOTAL_COST_BPS,
                net_return_bps=r["net_bps"],
                valid=r["valid"],
                rejection_reason=r["invalid_reason"],
            )
            fr_objects.append(fr)

        stats = compute_cell_stats(fr_objects)
        train_stats = compute_cell_stats([
            TickForwardReturn(
                signal_id=r["stress_event_id"], signal_ts=r["signal_ts"],
                target_venue=VENUE, target_symbol=r["target_symbol"],
                horizon_ms=r["horizon_ms"],
                entry_reference_price=r["entry_price"], forward_price=r["forward_price"],
                raw_return_bps=r["raw_return_bps"],
                direction_adjusted_return_bps=r["direction_adjusted_return_bps"],
                fee_bps=TOTAL_COST_BPS, net_return_bps=r["net_bps"],
                valid=r["valid"], rejection_reason=r["invalid_reason"],
            )
            for r in train_events
        ])
        hold_stats = compute_cell_stats([
            TickForwardReturn(
                signal_id=r["stress_event_id"], signal_ts=r["signal_ts"],
                target_venue=VENUE, target_symbol=r["target_symbol"],
                horizon_ms=r["horizon_ms"],
                entry_reference_price=r["entry_price"], forward_price=r["forward_price"],
                raw_return_bps=r["raw_return_bps"],
                direction_adjusted_return_bps=r["direction_adjusted_return_bps"],
                fee_bps=TOTAL_COST_BPS, net_return_bps=r["net_bps"],
                valid=r["valid"], rejection_reason=r["invalid_reason"],
            )
            for r in holdout_events
        ])

        # Gates
        gates_passed = True
        gate_reasons: List[str] = []

        if stats.valid_count < MIN_EVENTS_PER_CELL:
            gates_passed = False
            gate_reasons.append(f"insufficient_events:{stats.valid_count}<{MIN_EVENTS_PER_CELL}")

        if stats.mean_net_bps is not None and stats.mean_net_bps <= 0:
            gates_passed = False
            gate_reasons.append(f"mean_net_not_positive:{stats.mean_net_bps:.2f}")

        if stats.median_net_bps is not None and stats.median_net_bps <= 0:
            gates_passed = False
            gate_reasons.append(f"median_net_not_positive:{stats.median_net_bps:.2f}")

        if stats.win_rate is not None and stats.win_rate < WIN_RATE_THRESHOLD:
            gates_passed = False
            gate_reasons.append(f"win_rate_too_low:{stats.win_rate:.4f}<{WIN_RATE_THRESHOLD}")

        if stats.worst_decile_net_bps is not None and stats.worst_decile_net_bps <= WORST_DECILE_THRESHOLD:
            gates_passed = False
            gate_reasons.append(f"worst_decile_too_low:{stats.worst_decile_net_bps:.2f}<={WORST_DECILE_THRESHOLD}")

        cell_results[gk] = {
            "group_key": gk,
            "source_symbol": gk.split("->")[0],
            "target_symbol": gk.split("->")[1].split("/")[0],
            "stress_window_seconds": int(gk.split("/")[1].replace("s", "")),
            "direction": gk.split("/")[2],
            "horizon_ms": int(gk.split("/")[3].replace("ms", "")),
            "valid_count": stats.valid_count,
            "mean_net_bps": stats.mean_net_bps,
            "median_net_bps": stats.median_net_bps,
            "win_rate": stats.win_rate,
            "worst_decile_net_bps": stats.worst_decile_net_bps,
            "gates_passed": gates_passed,
            "gate_reasons": gate_reasons,
            "train_count": train_stats.valid_count,
            "train_mean_net_bps": train_stats.mean_net_bps,
            "holdout_count": hold_stats.valid_count,
            "holdout_mean_net_bps": hold_stats.mean_net_bps,
            "holdout_win_rate": hold_stats.win_rate,
            "cell_verdict": _cell_verdict(gates_passed, stats.valid_count),
        }

    # Phase 7: Baseline
    print(f"\n[Phase 7] Computing baseline...")
    baseline_results: Dict[str, Dict[str, Any]] = {}
    for gk in all_cell_keys:
        cell = cell_results.get(gk, {})
        n = cell.get("valid_count", 0)
        if n < MIN_EVENTS_PER_CELL:
            continue

        # Generate baseline for this cell's source/target
        src_sym = cell.get("source_symbol", "")
        all_targets = {sym: all_ticks.get(sym, []) for sym in TARGET_SYMBOLS}

        baseline_frs = generate_baseline_events(
            all_targets, src_sym, n, seed=args.seed,
        )

        baseline_stats = compute_cell_stats(baseline_frs)

        baseline_mean = baseline_stats.mean_net_bps
        cell_mean = cell.get("mean_net_bps")
        baseline_delta = None
        if cell_mean is not None and baseline_mean is not None:
            baseline_delta = float(cell_mean) - float(baseline_mean)

        baseline_results[gk] = {
            "group_key": gk,
            "baseline_n": baseline_stats.valid_count,
            "baseline_mean_net_bps": baseline_mean,
            "baseline_median_net_bps": baseline_stats.median_net_bps,
            "baseline_win_rate": baseline_stats.win_rate,
            "cell_mean_net_bps": cell_mean,
            "baseline_delta_bps": baseline_delta,
        }

        # Apply baseline gate
        if baseline_delta is not None and baseline_delta < BASELINE_DELTA_BPS:
            if "gates_passed" in cell_results[gk] and cell_results[gk]["gates_passed"]:
                cell_results[gk]["gates_passed"] = False
                cell_results[gk]["gate_reasons"].append(
                    f"baseline_delta_too_low:{baseline_delta:.2f}<{BASELINE_DELTA_BPS}"
                )
                cell_results[gk]["cell_verdict"] = _cell_verdict(False, n)

    # Phase 8: Null test
    print(f"\n[Phase 8] Running null tests ({args.null_iterations} iterations)...")
    null_results: Dict[str, Dict[str, Any]] = {}
    for gk in all_cell_keys:
        cell = cell_results.get(gk, {})
        if not cell.get("gates_passed", False):
            null_results[gk] = {"p_value": None, "reason": "gates_not_passed"}
            continue

        n = cell.get("valid_count", 0)
        if n < MIN_EVENTS_PER_CELL:
            null_results[gk] = {"p_value": None, "reason": "insufficient_events"}
            continue

        # Get net returns
        cell_events = [r for r in forward_rows if r["group_key"] == gk and r["valid"] and r["train_or_holdout"] == "train"]
        net_returns: List[float] = []
        for r in cell_events:
            nb = r.get("net_bps")
            if nb is not None and math.isfinite(nb):
                net_returns.append(float(nb))

        if len(net_returns) < 2:
            null_results[gk] = {"p_value": None, "reason": "insufficient_returns"}
            continue

        null_res = run_null_test(net_returns, iterations=args.null_iterations, seed=args.seed)
        null_results[gk] = null_res

    # Phase 9: FDR
    print(f"\n[Phase 9] FDR correction...")
    pvalues: List[Tuple[str, float]] = []
    for gk in all_cell_keys:
        nr = null_results.get(gk, {})
        pv = nr.get("p_value")
        if pv is not None and isinstance(pv, (int, float)) and 0 <= pv <= 1:
            pvalues.append((gk, float(pv)))

    fdr_results_raw = apply_by_fdr(pvalues, alpha=FDR_ALPHA)

    fdr_results: Dict[str, Dict[str, Any]] = {}
    for gk in all_cell_keys:
        if gk in fdr_results_raw:
            fdr_results[gk] = fdr_results_raw[gk]
        else:
            fdr_results[gk] = {"fdr_passed": None, "reason": "no_pvalue"}

    # Phase 10: Holdout evaluation
    print(f"\n[Phase 10] Holdout evaluation...")
    holdout_results: Dict[str, Dict[str, Any]] = {}
    for gk in all_cell_keys:
        cell = cell_results.get(gk, {})
        hold_n = cell.get("holdout_count", 0)
        hold_mean = cell.get("holdout_mean_net_bps")
        hold_wr = cell.get("holdout_win_rate")

        holdout_pass = True
        hold_reasons: List[str] = []

        if hold_n < MIN_EVENTS_HOLDOUT:
            hold_reasons.append(f"underpowered:{hold_n}<{MIN_EVENTS_HOLDOUT}")
            holdout_pass = False

        if hold_mean is not None and hold_mean <= 0:
            hold_reasons.append(f"holdout_mean_not_positive:{hold_mean:.2f}")
            holdout_pass = False

        if hold_n >= MIN_EVENTS_HOLDOUT and hold_wr is not None and hold_wr < WIN_RATE_THRESHOLD:
            hold_reasons.append(f"holdout_wr_too_low:{hold_wr:.4f}<{WIN_RATE_THRESHOLD}")
            holdout_pass = False

        holdout_results[gk] = {
            "holdout_n": hold_n,
            "holdout_mean_net_bps": hold_mean,
            "holdout_win_rate": hold_wr,
            "holdout_passed": holdout_pass,
            "holdout_reasons": hold_reasons,
        }

    # Phase 11: Event vector reconciliation
    print(f"\n[Phase 11] Event vector reconciliation...")
    reconciled = True
    reconcilation_errors: List[str] = []
    for gk, cell in cell_results.items():
        if cell["valid_count"] == 0:
            continue
        # Recompute from forward_rows
        cell_events = [r for r in forward_rows if r["group_key"] == gk and r["valid"]]
        nets = [float(r["net_bps"]) for r in cell_events if r["net_bps"] is not None and math.isfinite(r["net_bps"])]

        if len(nets) != cell["valid_count"]:
            reconcilation_errors.append(f"{gk}: count mismatch {len(nets)} vs {cell['valid_count']}")
            reconciled = False
            continue

        if nets:
            computed_mean = sum(nets) / len(nets)
            computed_median = statistics.median(nets) if len(nets) > 0 else None
            computed_wins = sum(1 for n in nets if n > 0)
            computed_wr = computed_wins / len(nets)

            if abs(computed_mean - cell["mean_net_bps"]) > 0.01:
                reconcilation_errors.append(f"{gk}: mean mismatch {computed_mean:.4f} vs {cell['mean_net_bps']}")
                reconciled = False

    reconciliation_result = {
        "reconciled": reconciled,
        "errors": reconcilation_errors,
        "cells_checked": len([c for c in cell_results.values() if c["valid_count"] > 0]),
    }

    if not reconciled:
        print(f"  RECONCILIATION FAILED: {len(reconcilation_errors)} errors")
        verdict = "ARCHIVE_RUN_INVALIDATED_BUG_COMPROMISED"
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       early_stop=f"reconciliation_failed_{len(reconcilation_errors)}_errors")
        print(f"\n=== VERDICT: {verdict} ===")
        return

    # Phase 12: Aggregate cell verdicts and family verdict
    print(f"\n[Phase 12] Final verdict...")
    powered_cells = 0
    underpowered_cells = 0
    gates_passed_cells = 0
    null_passed_cells = 0
    fdr_passed_cells = 0
    holdout_passed_cells = 0
    all_gates_passed_cells = 0

    best_cells_mean: List[Dict[str, Any]] = []
    best_cells_delta: List[Dict[str, Any]] = []

    for gk in all_cell_keys:
        cell = cell_results.get(gk, {})
        n = cell.get("valid_count", 0)

        if n >= MIN_EVENTS_PER_CELL:
            powered_cells += 1
        else:
            underpowered_cells += 1

        if cell.get("gates_passed", False):
            gates_passed_cells += 1

            # Check null
            nr = null_results.get(gk, {})
            pv = nr.get("p_value")
            null_pass = pv is not None and isinstance(pv, (int, float)) and float(pv) <= NULL_ALPHA

            if null_pass:
                null_passed_cells += 1

                # Check FDR
                fr_res = fdr_results.get(gk, {})
                fdr_pass = fr_res.get("fdr_passed", False)
                if fdr_pass:
                    fdr_passed_cells += 1

                    # Check holdout
                    hr = holdout_results.get(gk, {})
                    hold_pass = hr.get("holdout_passed", False)
                    if hold_pass:
                        holdout_passed_cells += 1
                        all_gates_passed_cells += 1

        # Collect best cells
        mean_n = cell.get("mean_net_bps")
        if mean_n is not None and n >= MIN_EVENTS_PER_CELL:
            best_cells_mean.append({
                "group_key": gk,
                "mean_net_bps": mean_n,
                "n": n,
            })

        bl = baseline_results.get(gk, {})
        delta = bl.get("baseline_delta_bps")
        if delta is not None and n >= MIN_EVENTS_PER_CELL:
            best_cells_delta.append({
                "group_key": gk,
                "baseline_delta_bps": delta,
                "n": n,
            })

    best_cells_mean.sort(key=lambda x: x["mean_net_bps"], reverse=True)
    best_cells_delta.sort(key=lambda x: x["baseline_delta_bps"], reverse=True)

    # Determine family verdict
    if all_gates_passed_cells > 0:
        verdict = "CANDIDATE_FOR_LONGER_OBSERVATION_ARCHIVE_ONLY"
    elif powered_cells > 0 and all(
        cell_results.get(gk, {}).get("mean_net_bps") is not None
        and cell_results.get(gk, {}).get("mean_net_bps", 0) is not None
        and float(cell_results.get(gk, {}).get("mean_net_bps", -1) or -1) <= 0
        for gk in all_cell_keys
        if cell_results.get(gk, {}).get("valid_count", 0) >= MIN_EVENTS_PER_CELL
    ):
        verdict = "SIGNAL_ABSENCE_AT_COST"
    elif powered_cells > 0:
        verdict = "REJECTED_ARCHIVE_STRESS_BETA_LAG_V0"
    else:
        verdict = "NEEDS_MORE_DATA_ARCHIVE_STRESS_WINDOWS"

    print(f"  Powered cells: {powered_cells}")
    print(f"  Underpowered: {underpowered_cells}")
    print(f"  Gates passed: {gates_passed_cells}")
    print(f"  Null passed: {null_passed_cells}")
    print(f"  FDR passed: {fdr_passed_cells}")
    print(f"  Holdout passed: {holdout_passed_cells}")
    print(f"  All gates: {all_gates_passed_cells}")
    print(f"  Final verdict: {verdict}")

    # Phase 13: Write report
    print(f"\n[Phase 13] Writing report...")

    # Build label data for report
    label_rows: List[Dict[str, Any]] = []
    for lbl in windowed:
        label_rows.append({
            "label_id": lbl.label_id,
            "source_symbol": lbl.source_symbol,
            "stress_start_ns": lbl.stress_start_ns,
            "stress_end_ns": lbl.stress_end_ns,
            "stress_window_seconds": lbl.stress_window_seconds,
            "source_move_bps": lbl.source_move_bps,
            "direction": lbl.direction,
            "independent_window_id": lbl.independent_window_id,
            "rule_name": lbl.rule_name,
        })

    window_rows: List[Dict[str, Any]] = []
    for wid in sorted(independent_window_ids):
        window_labels = [l for l in windowed if l.independent_window_id == wid]
        window_rows.append({
            "window_id": wid,
            "label_count": len(window_labels),
            "first_ts": min(l.stress_end_ns for l in window_labels) if window_labels else None,
            "last_ts": max(l.stress_end_ns for l in window_labels) if window_labels else None,
        })

    # Build summary
    summary = {
        "run_id": run_id,
        "study_id": "cross_asset_beta_lag_archive_v0",
        "precommitment_hash": precommitment_sha,
        "git_sha": git_sha,
        "completion_time": _now_utc_iso(),
        "seed": args.seed,
        "null_iterations": args.null_iterations,
        "total_stress_labels": len(windowed),
        "independent_windows": len(independent_window_ids),
        "usable_labels": len(usable_labels),
        "all_target_windows": all_target_windows,
        "total_valid_events": sum(c.get("valid_count", 0) for c in cell_results.values()),
        "powered_cells": powered_cells,
        "underpowered_cells": underpowered_cells,
        "gates_passed_cells": gates_passed_cells,
        "null_passed_cells": null_passed_cells,
        "fdr_passed_cells": fdr_passed_cells,
        "holdout_passed_cells": holdout_passed_cells,
        "all_gates_passed_cells": all_gates_passed_cells,
        "best_cells_mean_net": best_cells_mean[:10],
        "best_cells_baseline_delta": best_cells_delta[:10],
        "final_verdict": verdict,
        "registry_updated": False,
        "test_results": {"run": 0, "passed": 0, "failed": 0},
    }

    _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict, summary=summary)
    print(f"\n=== DONE: {verdict} ===")
    print(f"Report: {output_dir}")


def _cell_verdict(gates_passed: bool, count: int) -> str:
    if not gates_passed:
        return "GATES_FAILED"
    if count < MIN_EVENTS_PER_CELL:
        return "NEEDS_MORE_DATA_CELL"
    return "GATES_PASSED"


def _write_early_stop(output_dir: Path, run_id: str, reason: str, git_sha: str) -> None:
    summary = {
        "run_id": run_id,
        "study_id": "cross_asset_beta_lag_archive_v0",
        "final_verdict": reason,
        "early_stop_reason": reason,
        "git_sha": git_sha,
        "completion_time": _now_utc_iso(),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "PRECOMMITMENT_SHA256.txt", "NOT_REACHED")
    atomic_write_text(output_dir / "FINAL_REPORT.md",
                      f"# Cross-Asset Beta-Lag Archive v0 — EARLY STOP\n\n**Reason:** {reason}\n\n**Run ID:** {run_id}\n")


def _write_availability_report(
    output_dir: Path, run_id: str, git_sha: str, precommitment_sha: str,
    availability: Dict[str, Any], common_start: Any, common_end: Any, common_days: int,
) -> None:
    atomic_write_json(output_dir / "preflight.json", {
        "run_id": run_id, "git_sha": git_sha, "precommitment_hash": precommitment_sha,
    })
    atomic_write_json(output_dir / "archive_availability.json", availability)
    atomic_write_json(output_dir / "coverage_summary.json", {
        "common_start": common_start,
        "common_end": common_end,
        "common_days": common_days,
    })


def _write_data_report(
    output_dir: Path, run_id: str, git_sha: str, precommitment_sha: str,
    availability: Dict[str, Any], file_manifest: List, per_sym_coverage: Dict,
    missing_symbols: List,
) -> None:
    atomic_write_json(output_dir / "preflight.json", {
        "run_id": run_id, "git_sha": git_sha, "precommitment_hash": precommitment_sha,
    })
    atomic_write_json(output_dir / "archive_availability.json", availability)
    atomic_write_json(output_dir / "archive_file_manifest.json", file_manifest)
    atomic_write_json(output_dir / "coverage_summary.json", {
        "per_symbol_coverage": per_sym_coverage,
        "missing_symbols": missing_symbols,
    })


def _write_stress_label_report(
    output_dir: Path, labels: List[StressLabel], window_ids: List[str],
) -> None:
    label_rows = [{
        "label_id": l.label_id,
        "source_symbol": l.source_symbol,
        "stress_end_ns": l.stress_end_ns,
        "stress_window_seconds": l.stress_window_seconds,
        "source_move_bps": l.source_move_bps,
        "direction": l.direction,
        "independent_window_id": l.independent_window_id,
    } for l in labels]
    atomic_write_jsonl(output_dir / "stress_labels.jsonl", label_rows)
    atomic_write_json(output_dir / "independent_windows.json", {
        "window_count": len(window_ids),
        "window_ids": sorted(window_ids),
    })


def _write_summary(
    output_dir: Path, run_id: str, git_sha: str, precommitment_sha: str,
    verdict: str,
    *,
    early_stop: Optional[str] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> None:
    if summary is None:
        summary = {
            "run_id": run_id,
            "study_id": "cross_asset_beta_lag_archive_v0",
            "precommitment_hash": precommitment_sha,
            "git_sha": git_sha,
            "final_verdict": verdict,
            "early_stop_reason": early_stop,
            "completion_time": _now_utc_iso(),
        }
    atomic_write_json(output_dir / "summary.json", summary)


if __name__ == "__main__":
    main()
