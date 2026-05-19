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
    download_daily_klines_1m,
    scan_archive_availability,
    compute_common_calendar,
    parse_agg_trade_csv,
    parse_1m_klines_csv,
    _iter_date_range,
    _sha256_bytes,
    _sha256_file,
    compute_kline_candidate_days,  # deprecated — kept for A/B comparison
    estimate_file_size_mb,
    estimate_kline_file_size_mb,
)
from .kline_prefilter_v1 import (
    compute_kline_candidate_days_v1,
    compute_kline_candidate_days_deprecated,  # A/B comparison
)
from .binance_vision_archive import append_ticks_jsonl, tick_file_path
from .streaming_stress_labels import generate_stress_labels_streaming
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
    run_null_test_gpu,
    apply_by_fdr,
    reconcile_event_vector,
    write_report,
    _get_git_sha,
    _now_utc_iso,
    _sha256_json,
    MS_TO_NS,
    ENTRY_DELAY_NS,
    build_stress_day_download_plan,

    # Checkpoint functions
    CHECKPOINT_PHASES,
    write_checkpoint,
    write_checkpoint_manifest,
    load_checkpoint_manifest,
    compute_resume_phase,
    validate_checkpoint_config,
)

from .run_artifacts import (
    create_run_id,
    create_run_dir,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    safe_output_dir,
)


def _load_checkpoint_payload(output_dir: Path, phase: str) -> Dict[str, Any]:
    """Load a checkpoint artifact payload if it exists and is valid."""
    cp_path = output_dir / f"checkpoint_phase_{phase}.json"
    if not cp_path.exists():
        return {}
    try:
        data = json.loads(cp_path.read_text("utf-8"))
        return data.get("payload", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _load_ts_prices_from_jsonl(path: Path) -> tuple:
    """Load (timestamps, prices) arrays from a tick JSONL file.

    Reads only ts_event and price fields, skips non-finite prices.
    Returns (numpy.ndarray[int64], numpy.ndarray[float64]).
    """
    import json as _json  # noqa: PLC0415
    import math as _math  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    ts_list: list[int] = []
    pr_list: list[float] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            row = _json.loads(line)
            price = row.get("price")
            ts = row.get("ts_event")
            if price is None or ts is None:
                continue
            if not _math.isfinite(price):
                continue
            ts_list.append(int(ts))
            pr_list.append(float(price))
    return np.array(ts_list, dtype=np.int64), np.array(pr_list, dtype=np.float64)


def _reconstruct_labels_from_checkpoint(output_dir: Path) -> List[StressLabel]:
    """Reconstruct StressLabel objects from checkpoint '05_independent_windows'."""
    payload = _load_checkpoint_payload(output_dir, "05_independent_windows")
    rows = payload.get("label_rows", [])
    labels: List[StressLabel] = []
    for r in rows:
        labels.append(StressLabel(
            label_id=r["label_id"],
            source_symbol=r["source_symbol"],
            stress_end_ns=r["stress_end_ns"],
            stress_window_seconds=r["stress_window_seconds"],
            source_move_bps=r["source_move_bps"],
            direction=r["direction"],
            independent_window_id=r["independent_window_id"],
            rule_name=r.get("rule_name", "checkpoint_reconstructed"),
            # Fields not stored in checkpoint (not needed for resume)
            stress_start_ns=0,
            source_start_price=0.0,
            source_end_price=0.0,
        ))
    return labels


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
    parser.add_argument(
        "--prefilter-source-klines", action="store_true", default=True,
        help="Enable source kline stress-day prefilter (default: enabled)",
    )
    parser.add_argument(
        "--no-prefilter-source-klines", action="store_false", dest="prefilter_source_klines",
        help="Disable source kline prefilter (brute-force download)",
    )
    parser.add_argument(
        "--max-planned-download-gb", type=float, default=5.0,
        help="Max planned download in GB after prefilter (default: 5.0)",
    )
    parser.add_argument(
        "--force-refresh-klines", action="store_true", default=False,
        help="Force re-download of cached kline files",
    )
    parser.add_argument(
        "--force-refresh-aggtrades", action="store_true", default=False,
        help="Force re-download of cached aggTrade files",
    )
    parser.add_argument(
        "--resume", action="store_true", default=False,
        help="Resume from last completed checkpoint",
    )
    parser.add_argument(
        "--null-engine", choices=["cpu", "gpu"], default="cpu",
        help="Null test engine: cpu (default) or gpu. GPU requires CUDA.",
    )
    parser.add_argument(
        "--null-device", default="cuda:0",
        help="CUDA device for GPU null (default: cuda:0)",
    )
    parser.add_argument(
        "--null-batch-size", type=int, default=0,
        help="GPU null batch size (0 = default). CPU path ignores this.",
    )
    args = parser.parse_args()

    # ── GPU null engine check ────────────────────────────────────────────────
    null_engine: str = args.null_engine
    null_device: str = args.null_device
    null_batch_size: int = args.null_batch_size if args.null_batch_size > 0 else 4096

    if args.null_engine == "gpu":
        try:
            from .permutation_null_gpu import check_cuda_available
            cuda_ok, cuda_reason = check_cuda_available(args.null_device)
        except ImportError:
            cuda_ok, cuda_reason = False, "permutation_null_gpu_import_failed"
        if not cuda_ok:
            print(f"GPU_UNAVAILABLE_DIAGNOSTIC: {cuda_reason}")
            print("Falling back to CPU null.")
            null_engine = "cpu"
        else:
            print(f"GPU null enabled: {null_device} (batch_size={null_batch_size})")
    else:
        print("Null engine: CPU")

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

    # ── Resume logic ──────────────────────────────────────────────────────────
    completed_phases: List[str] = []
    resume_phase: Optional[str] = None
    prefilter_data_reloaded = False
    run_id: str = ""
    output_dir: Path = Path(args.out)

    if args.resume:
        # Find the most recent existing run directory with a valid checkpoint
        base = Path(args.out)
        if base.exists():
            candidate_dirs = sorted(
                [d for d in base.iterdir() if d.is_dir()],
                key=lambda d: d.name,
                reverse=True,
            )
            for cand in candidate_dirs:
                is_valid, reason = validate_checkpoint_config(
                    cand,
                    git_sha=git_sha,
                    precommitment_sha="",
                    null_engine=null_engine,
                )
                if is_valid:
                    rp = compute_resume_phase(cand)
                    if rp:
                        output_dir = cand
                        run_id = cand.name
                        resume_phase = rp
                        manifest = load_checkpoint_manifest(cand)
                        if manifest:
                            completed_phases = manifest.get("completed_phases", [])
                        print(f"\n[Resume] Found valid checkpoint in {output_dir}")
                        print(f"  Resume phase: {resume_phase}")
                        print(f"  Completed phases: {completed_phases}")
                        break
                    else:
                        print(f"\n[Resume] {cand.name}: manifest exists but no resumable phase.")
        if not run_id:
            print(f"\n[Resume] No valid checkpoint found in {args.out} — starting fresh.")
            resume_phase = None
            run_id = create_run_id("cross_asset_beta_lag_archive")
            output_dir = Path(args.out) / run_id
    else:
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

    # Checkpoint: 01_availability
    write_checkpoint(output_dir, "01_availability",
        {"common_start": common_start, "common_end": common_end,
         "common_days": common_days, "all_symbols": ALL_SYMBOLS,
         "availability_summary": {sym: {"days": v.get("days_available", 0)} for sym, v in availability.items()}},
        git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
    completed_phases.append("01_availability")

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

    # Estimate brute-force data volume (audit only)
    date_list = _iter_date_range(common_start or CALENDAR_START, common_end or CALENDAR_END)
    brute_files = len(ALL_SYMBOLS) * len(date_list)
    brute_mb = sum(estimate_file_size_mb(sym) for sym in ALL_SYMBOLS) * len(date_list)
    print(f"  Brute-force estimate: {brute_files} files ({brute_mb:.0f} MB)")

    if args.prefilter_source_klines:
        # ── Prefilter path ────────────────────────────────────────────────
        print(f"\n[Phase 4A] Downloading source klines ({len(SOURCE_SYMBOLS)} symbols)...")
        source_kline_data: Dict[str, List[Dict[str, Any]]] = {}
        kline_manifest: List[Dict[str, Any]] = []
        
        for src_sym in SOURCE_SYMBOLS:
            print(f"  Downloading 1m klines for {src_sym}...")
            sym_klines: List[Dict[str, Any]] = []
            for d in date_list:
                force = args.force_refresh_klines
                kzip, sha = download_daily_klines_1m(src_sym, d, cache=not force)
                if kzip is not None:
                    rows = parse_1m_klines_csv(kzip)
                    sym_klines.extend(rows)
                    kline_manifest.append({
                        "symbol": src_sym, "date": d, "source": "klines_1m",
                        "status": "downloaded", "sha256": sha,
                        "size_bytes": len(kzip), "row_count": len(rows),
                    })
                else:
                    kline_manifest.append({
                        "symbol": src_sym, "date": d, "source": "klines_1m",
                        "status": "not_found", "sha256": None,
                        "size_bytes": None, "row_count": 0,
                    })
            source_kline_data[src_sym] = sym_klines
            print(f"    {len(sym_klines)} klines for {src_sym}")

        kline_kb = sum(m.get("size_bytes", 0) or 0 for m in kline_manifest) / 1024
        print(f"  Kline data: {kline_kb:.0f} KB")

        # Phase 4B: Kline prefilter (V1 — per-bar high-low range)
        print(f"\n[Phase 4B] Computing kline stress-day prefilter (V1 — per-bar HL >= 30bps)...")
        candidate_dates_set = compute_kline_candidate_days_v1(source_kline_data)
        print(f"  Candidate stress days (V1): {len(candidate_dates_set)}")

        # A/B comparison: also compute the deprecated daily-range filter
        candidate_dates_deprecated = compute_kline_candidate_days_deprecated(source_kline_data)
        if candidate_dates_deprecated != candidate_dates_set:
            print(f"  Candidate stress days (deprecated daily-range): {len(candidate_dates_deprecated)}")
            diff = candidate_dates_deprecated - candidate_dates_set
            print(f"  Deprecated filter over-selects {len(diff)} additional days")

        if not candidate_dates_set:
            _write_prefilter_report(output_dir, run_id, git_sha, precommitment_sha,
                                    availability, {}, kline_manifest, [],
                                    brute_files, brute_mb)
            verdict = "NEEDS_MORE_DATA_ARCHIVE_NO_KLINE_STRESS_DAYS"
            _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                           early_stop="zero_kline_candidate_days")
            print(f"\n=== VERDICT: {verdict} ===")
            return

        # Convert set of dates to the list-of-dicts format expected by build_stress_day_download_plan
        candidate_days = [
            {"date": d, "source_symbol": "BTCUSDT", "reason": "hl_per_bar_ge_30bps"}
            for d in sorted(candidate_dates_set)
        ]

        # Phase 4C: Build download plan
        print(f"\n[Phase 4C] Building aggTrade download plan...")
        dl_plan = build_stress_day_download_plan(
            candidate_days, ALL_SYMBOLS, SOURCE_SYMBOLS, date_list,
        )
        planned_mb = dl_plan["total_estimated_mb"]
        planned_files = dl_plan["required_file_count"]
        reduction_ratio = dl_plan["reduction_ratio"]

        print(f"  Planned files: {planned_files} ({planned_mb:.1f} MB)")
        print(f"  Reduction ratio: {reduction_ratio}x")

        # Phase 4D: Volume gate on planned download
        print(f"\n[Phase 4D] Checking planned download volume...")
        max_planned_mb = args.max_planned_download_gb * 1024
        if planned_mb > max_planned_mb:
            print(f"  PREFILTERED VOLUME EXCEEDS CAP: {planned_mb:.1f} > {max_planned_mb:.0f} MB")
            _write_prefilter_report(output_dir, run_id, git_sha, precommitment_sha,
                                    availability, dl_plan, kline_manifest, candidate_days,
                                    brute_files, brute_mb)
            verdict = "ARCHIVE_PREFILTERED_DATA_VOLUME_TOO_LARGE"
            _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                           early_stop=f"prefiltered_volume_{planned_mb:.0f}mb_exceeds_{max_planned_mb:.0f}mb")
            print(f"\n=== VERDICT: {verdict} ===")
            return

        # Phase 4E: Download planned aggTrades
        print(f"\n[Phase 4E] Downloading planned aggTrades...")
        all_tick_counts: Dict[str, int] = {sym: 0 for sym in ALL_SYMBOLS}
        file_manifest: List[Dict[str, Any]] = []
        total_files = 0
        failed_files = 0

        for entry in dl_plan["required_files"]:
            sym = entry["symbol"]
            d = entry["date"]
            force = args.force_refresh_aggtrades

            data, sha = download_daily_agg_trades(sym, d, cache=not force)
            if data is None:
                failed_files += 1
                file_manifest.append({
                    "symbol": sym, "date": d, "source": "aggTrades",
                    "status": "not_found", "sha256": None, "size_bytes": None, "row_count": 0,
                })
                continue

            ticks = parse_agg_trade_csv(data, sym, venue=VENUE)
            append_ticks_jsonl(tick_file_path(output_dir, sym), ticks)
            all_tick_counts[sym] = all_tick_counts.get(sym, 0) + len(ticks)

            file_manifest.append({
                "symbol": sym, "date": d, "source": "aggTrades",
                "status": "downloaded", "sha256": sha,
                "size_bytes": len(data), "row_count": len(ticks),
            })
            total_files += 1

        print(f"  Files: {total_files} OK, {failed_files} failed")
        total_tick_count = sum(all_tick_counts.values())
        print(f"  Total ticks: {total_tick_count}")

        # Check per-symbol coverage
        missing_symbols = [sym for sym in ALL_SYMBOLS if all_tick_counts.get(sym, 0) == 0]
        per_sym_coverage: Dict[str, Dict[str, Any]] = {}
        for sym in ALL_SYMBOLS:
            per_sym_coverage[sym] = {
                "tick_count": all_tick_counts.get(sym, 0),
                "first_ts": None,
                "last_ts": None,
            }

        # Phase 4F: Exact aggTrade stress label reconstruction
        print(f"\n[Phase 4F] Reconstructing exact stress labels from aggTrades...")
        all_labels: List[StressLabel] = []
        for src_sym in SOURCE_SYMBOLS:
            jsonl = tick_file_path(output_dir, src_sym)
            if not jsonl.exists() or jsonl.stat().st_size == 0:
                print(f"  WARNING: No JSONL tick file for {src_sym}")
                continue
            for rule_name, lookback_sec, threshold in STRESS_RULES:
                labels = generate_stress_labels_streaming(
                    jsonl, src_sym,
                    lookback_seconds=lookback_sec,
                    threshold_bps=threshold,
                    StressLabel=StressLabel,
                )
                all_labels.extend(labels)
                print(f"  {src_sym} {rule_name}: {len(labels)} labels")

        # Checkpoint: 04_stress_labels (before dedup — raw labels exist)
        write_checkpoint(output_dir, "04_stress_labels",
            {"total_labels": len(all_labels), "source_symbols": SOURCE_SYMBOLS},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("04_stress_labels")

        if not all_labels:
            _write_prefilter_report(output_dir, run_id, git_sha, precommitment_sha,
                                    availability, dl_plan, kline_manifest, candidate_days,
                                    brute_files, brute_mb)
            verdict = "NEEDS_MORE_DATA_ARCHIVE_NO_EXACT_STRESS_LABELS"
            _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                           early_stop="zero_exact_stress_labels")
            print(f"\n=== VERDICT: {verdict} ===")
            return

        # Write prefilter artifacts
        _write_prefilter_artifacts(
            output_dir, candidate_days, dl_plan, kline_manifest, all_labels,
            brute_files, brute_mb, planned_mb,
        )

        # Checkpoint: 02_kline_prefilter + 03_aggtrades + 04_stress_labels
        write_checkpoint(output_dir, "02_kline_prefilter",
            {"candidate_days": len(candidate_days), "reduction_ratio": reduction_ratio,
             "planned_files": planned_files, "planned_mb": planned_mb},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("02_kline_prefilter")
        write_checkpoint(output_dir, "03_aggtrades",
            {"total_files": total_files, "failed_files": failed_files,
             "per_sym_tick_counts": {sym: all_tick_counts.get(sym, 0) for sym in ALL_SYMBOLS}},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("03_aggtrades")

        # Note: evaluation continues from Phase 3 below
        phase_label = "[Phase 4G]"
    else:
        # ── Brute-force path (prefilter disabled) ─────────────────────────
        if brute_mb > 5000:
            print(f"  BRUTE-FORCE VOLUME TOO LARGE: ~{brute_mb:.0f} MB")
            _write_availability_report(output_dir, run_id, git_sha, precommitment_sha,
                                       availability, common_start, common_end, common_days)
            verdict = "NEEDS_MORE_DATA_ARCHIVE_AVAILABILITY"
            _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                           early_stop=f"archive_data_volume_too_large_{brute_mb:.0f}mb")
            print(f"\n=== VERDICT: {verdict} ===")
            print(f"Use --prefilter-source-klines to enable kline prefilter.")
            return

        print(f"\n[Phase 2] Downloading all aggTrades ({len(ALL_SYMBOLS)} symbols)...")
        all_ticks = {sym: [] for sym in ALL_SYMBOLS}
        file_manifest = []
        total_files = 0
        failed_files = 0

        for sym in ALL_SYMBOLS:
            sym_ticks: List[TradeTickLite] = []
            for d in date_list:
                data, sha = download_daily_agg_trades(sym, d, cache=not args.force_refresh_aggtrades)
                if data is None:
                    failed_files += 1
                    file_manifest.append({"symbol": sym, "date": d, "source": "aggTrades",
                        "status": "not_found", "sha256": None, "size_bytes": None, "row_count": 0})
                    continue
                ticks = parse_agg_trade_csv(data, sym, venue=VENUE)
                sym_ticks.extend(ticks)
                file_manifest.append({"symbol": sym, "date": d, "source": "aggTrades",
                    "status": "downloaded", "sha256": sha, "size_bytes": len(data), "row_count": len(ticks)})
                total_files += 1
            all_ticks[sym] = sym_ticks

        missing_symbols = [sym for sym in ALL_SYMBOLS if not all_ticks.get(sym)]
        per_sym_coverage = {}
        for sym in ALL_SYMBOLS:
            t = all_ticks.get(sym, [])
            per_sym_coverage[sym] = {"tick_count": len(t), "first_ts": t[0].ts_event if t else None,
                                      "last_ts": t[-1].ts_event if t else None}

        # Generate stress labels from all source ticks
        print(f"\n[Phase 3] Generating stress labels...")
        all_labels = []
        for src_sym in SOURCE_SYMBOLS:
            src_ticks = all_ticks.get(src_sym, [])
            if not src_ticks:
                continue
            for rule_name, lookback_sec, threshold in STRESS_RULES:
                labels = generate_stress_labels(src_ticks, src_sym,
                    lookback_seconds=lookback_sec, threshold_bps=threshold)
                all_labels.extend(labels)

        phase_label = "[Phase 3]"

    # ── Common evaluation path (dedup, coverage, forward returns, null, FDR) ──
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

    # Checkpoint: 05_independent_windows
    label_rows_cp = [{
        "label_id": l.label_id, "source_symbol": l.source_symbol,
        "stress_end_ns": l.stress_end_ns, "stress_window_seconds": l.stress_window_seconds,
        "source_move_bps": l.source_move_bps, "direction": l.direction,
        "independent_window_id": l.independent_window_id, "rule_name": l.rule_name,
    } for l in windowed]
    write_checkpoint(output_dir, "05_independent_windows",
        {"window_count": len(independent_window_ids), "label_count": len(windowed),
         "label_rows": label_rows_cp},
        git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
    completed_phases.append("05_independent_windows")

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

    # Pre-load all target (timestamps, prices) from JSONL
    target_data = {}
    for sym in TARGET_SYMBOLS:
        jpath = tick_file_path(output_dir, sym)
        if jpath.exists() and jpath.stat().st_size > 0:
            ts, pr = _load_ts_prices_from_jsonl(jpath)
            target_data[sym] = (ts, pr)
            print(f"  {sym}: {len(ts)} ticks from JSONL")
        else:
            target_data[sym] = ([], [])

    for lbl in windowed:
        # Use max horizon for coverage
        max_horizon_ns = max(HORIZONS_MS) * MS_TO_NS
        all_covered, coverage_intervals = check_target_coverage_from_ts_prices(
            target_data,
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

    # Checkpoint: 06_coverage
    write_checkpoint(output_dir, "06_coverage",
        coverage_summary,
        git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
    completed_phases.append("06_coverage")

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

    all_cell_keys_list = all_cell_keys()

    # Phase 5: Compute forward returns (resume-aware)
    forward_rows: List[Dict[str, Any]] = []
    signal_rows: List[Dict[str, Any]] = []

    if "07_forward_returns" in completed_phases:
        print(f"\n[Phase 5] Loading forward returns from checkpoint...")
        payload = _load_checkpoint_payload(output_dir, "07_forward_returns")
        forward_rows = payload.get("forward_rows", [])
        print(f"  Forward return rows (from checkpoint): {len(forward_rows)}")
    else:
        print(f"\n[Phase 5] Computing forward returns...")

        # Per-target loop: load one target, process all labels, discard
        for tgt_sym in TARGET_SYMBOLS:
            jpath = tick_file_path(output_dir, tgt_sym)
            if not jpath.exists() or jpath.stat().st_size == 0:
                print(f"  SKIP {tgt_sym}: no JSONL")
                continue
            ts_arr, pr_arr = _load_ts_prices_from_jsonl(jpath)
            print(f"  {tgt_sym}: {len(ts_arr)} ticks for forward returns")

            for lbl in usable_labels:
                frs = compute_forward_returns_from_ts_prices(
                    lbl, ts_arr, pr_arr, tgt_sym, HORIZONS_MS,
                    TickForwardReturn=TickForwardReturn,
                    VENUE=VENUE,
                    ENTRY_DELAY_NS=ENTRY_DELAY_NS,
                    MS_TO_NS=MS_TO_NS,
                    FEE_BPS=FEE_BPS,
                    SLIPPAGE_BPS=SLIPPAGE_BPS,
                    QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
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
                        "entry_ts": fr.signal_ts,
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

        # Checkpoint: 07_forward_returns (CRITICAL)
        write_checkpoint(output_dir, "07_forward_returns",
            {"forward_row_count": len(forward_rows), "usable_label_count": len(usable_labels),
             "forward_rows": forward_rows},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("07_forward_returns")

    # Split train/holdout by stress event chronological order
    # Use unique stress events sorted by first signal_ts (works for both fresh and resumed)
    event_first_ts: Dict[str, int] = {}
    for r in forward_rows:
        eid = r["stress_event_id"]
        ts = r.get("signal_ts", 0)
        if eid not in event_first_ts or ts < event_first_ts[eid]:
            event_first_ts[eid] = ts
    sorted_event_ids = sorted(event_first_ts.keys(), key=lambda eid: event_first_ts[eid])
    split_idx = int(len(sorted_event_ids) * TRAIN_FRAC)
    train_event_ids = set(sorted_event_ids[:split_idx])

    for row in forward_rows:
        if row["stress_event_id"] in train_event_ids:
            row["train_or_holdout"] = "train"
        else:
            row["train_or_holdout"] = "holdout"

    # Phase 6: Cell evaluation (resume-aware)
    cell_results: Dict[str, Dict[str, Any]] = {}
    if "08_cell_results" in completed_phases:
        print(f"\n[Phase 6] Loading cell results from checkpoint...")
        payload = _load_checkpoint_payload(output_dir, "08_cell_results")
        cell_results = payload.get("cell_results", {})
        print(f"  Cell results (from checkpoint): {len(cell_results)} cells")
    else:
        print(f"\n[Phase 6] Cell evaluation...")

        for gk in all_cell_keys_list:
            cell_events = [r for r in forward_rows if r["group_key"] == gk and r["valid"]]
            train_events = [r for r in cell_events if r["train_or_holdout"] == "train"]
            holdout_events = [r for r in cell_events if r["train_or_holdout"] == "holdout"]

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

        # Checkpoint: 08_cell_results
        write_checkpoint(output_dir, "08_cell_results",
            {"cell_results": cell_results, "powered_cells": sum(1 for c in cell_results.values() if c.get("valid_count", 0) >= MIN_EVENTS_PER_CELL)},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("08_cell_results")

    # Phase 7: Baseline (resume-aware)
    baseline_results: Dict[str, Dict[str, Any]] = {}
    if "09_baseline" in completed_phases:
        print(f"\n[Phase 7] Loading baseline results from checkpoint...")
        payload = _load_checkpoint_payload(output_dir, "09_baseline")
        baseline_results = payload.get("baseline_results", {})
        print(f"  Baseline results (from checkpoint): {len(baseline_results)} cells")
    else:
        print(f"\n[Phase 7] Computing baseline...")
        for gk in all_cell_keys_list:
            cell = cell_results.get(gk, {})
            n = cell.get("valid_count", 0)
            if n < MIN_EVENTS_PER_CELL:
                continue

            src_sym = cell.get("source_symbol", "")
            baseline_targets = {}
            for tgt_sym in TARGET_SYMBOLS:
                jpath = tick_file_path(output_dir, tgt_sym)
                if jpath.exists() and jpath.stat().st_size > 0:
                    ts_arr, pr_arr = _load_ts_prices_from_jsonl(jpath)
                    baseline_targets[tgt_sym] = (ts_arr, pr_arr)
                else:
                    baseline_targets[tgt_sym] = ([], [])

            baseline_frs = generate_baseline_from_ts_prices(
                baseline_targets, src_sym, n,
                seed=args.seed,
                TickForwardReturn=TickForwardReturn,
                VENUE=VENUE,
                ENTRY_DELAY_NS=ENTRY_DELAY_NS,
                MS_TO_NS=MS_TO_NS,
                FEE_BPS=FEE_BPS,
                SLIPPAGE_BPS=SLIPPAGE_BPS,
                QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
                HORIZONS_MS=HORIZONS_MS,
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

        # Checkpoint: 09_baseline
        write_checkpoint(output_dir, "09_baseline",
            {"baseline_results": baseline_results},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("09_baseline")

    # Phase 8: Null test (resume-aware)
    null_results: Dict[str, Dict[str, Any]] = {}
    if "10_null" in completed_phases:
        print(f"\n[Phase 8] Loading null results from checkpoint...")
        payload = _load_checkpoint_payload(output_dir, "10_null")
        null_results = payload.get("null_results", {})
        print(f"  Null results (from checkpoint): {len(null_results)} cells")
    else:
        print(f"\n[Phase 8] Running null tests ({args.null_iterations} iterations)...")
        for gk in all_cell_keys_list:
            cell = cell_results.get(gk, {})
            if not cell.get("gates_passed", False):
                null_results[gk] = {"p_value": None, "reason": "gates_not_passed"}
                continue

            n = cell.get("valid_count", 0)
            if n < MIN_EVENTS_PER_CELL:
                null_results[gk] = {"p_value": None, "reason": "insufficient_events"}
                continue

            cell_events = [r for r in forward_rows if r["group_key"] == gk and r["valid"] and r["train_or_holdout"] == "train"]
            net_returns: List[float] = []
            for r in cell_events:
                nb = r.get("net_bps")
                if nb is not None and math.isfinite(nb):
                    net_returns.append(float(nb))

            if len(net_returns) < 2:
                null_results[gk] = {"p_value": None, "reason": "insufficient_returns"}
                continue

            if null_engine == "gpu":
                null_res = run_null_test_gpu(
                    net_returns, iterations=args.null_iterations, seed=args.seed,
                    device=null_device, batch_size=null_batch_size,
                )
            else:
                null_res = run_null_test(net_returns, iterations=args.null_iterations, seed=args.seed)
            null_results[gk] = null_res

        # Checkpoint: 10_null
        write_checkpoint(output_dir, "10_null",
            {"null_results": null_results},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("10_null")

    # Phase 9: FDR (resume-aware)
    fdr_results: Dict[str, Dict[str, Any]] = {}
    if "11_fdr" in completed_phases:
        print(f"\n[Phase 9] Loading FDR results from checkpoint...")
        payload = _load_checkpoint_payload(output_dir, "11_fdr")
        fdr_results = payload.get("fdr_results", {})
        print(f"  FDR results (from checkpoint): {len(fdr_results)} cells")
    else:
        print(f"\n[Phase 9] FDR correction...")
        pvalues: List[Tuple[str, float]] = []
        for gk in all_cell_keys_list:
            nr = null_results.get(gk, {})
            pv = nr.get("p_value")
            if pv is not None and isinstance(pv, (int, float)) and 0 <= pv <= 1:
                pvalues.append((gk, float(pv)))

        fdr_results_raw = apply_by_fdr(pvalues, alpha=FDR_ALPHA)

        for gk in all_cell_keys_list:
            if gk in fdr_results_raw:
                fdr_results[gk] = fdr_results_raw[gk]
            else:
                fdr_results[gk] = {"fdr_passed": None, "reason": "no_pvalue"}

        # Checkpoint: 11_fdr
        write_checkpoint(output_dir, "11_fdr",
            {"fdr_results": fdr_results, "pvalues_checked": len(pvalues)},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("11_fdr")

    # Phase 10: Holdout evaluation (resume-aware)
    holdout_results: Dict[str, Dict[str, Any]] = {}
    if "12_holdout" in completed_phases:
        print(f"\n[Phase 10] Loading holdout results from checkpoint...")
        payload = _load_checkpoint_payload(output_dir, "12_holdout")
        holdout_results = payload.get("holdout_results", {})
        print(f"  Holdout results (from checkpoint): {len(holdout_results)} cells")
    else:
        print(f"\n[Phase 10] Holdout evaluation...")
        for gk in all_cell_keys_list:
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

        # Checkpoint: 12_holdout
        write_checkpoint(output_dir, "12_holdout",
            {"holdout_results": holdout_results},
            git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine)
        completed_phases.append("12_holdout")

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

    for gk in all_cell_keys_list:
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
        for gk in all_cell_keys_list
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
    prefilter_summary = {}
    if args.prefilter_source_klines:
        prefilter_summary = {
            "full_calendar_retained": True,
            "kline_prefilter": "source_only_non_verdict_producing",
            "exact_stress_labels_from_aggtrades": True,
            "brute_force_mb": round(brute_mb, 1) if 'brute_mb' in dir() else 0,
            "planned_mb": round(planned_mb, 1) if 'planned_mb' in dir() else 0,
            "candidate_days": len(candidate_days) if 'candidate_days' in dir() else 0,
            "exact_stress_labels": len(windowed) if 'windowed' in dir() else 0,
            "usable_windows": all_target_windows if 'all_target_windows' in dir() else 0,
            "evaluation_reached": True,
        }

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
        "prefilter_summary": prefilter_summary,
        "null_engine": null_engine,
        "null_device": null_device,
        "cuda_available": null_engine == "gpu",
        "completed_phases": completed_phases,
    }

    _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict, summary=summary)

    # Write checkpoint manifest
    write_checkpoint_manifest(
        output_dir, completed_phases,
        git_sha=git_sha, precommitment_sha=precommitment_sha, null_engine=null_engine,
    )
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


def _write_prefilter_report(
    output_dir: Path, run_id: str, git_sha: str, precommitment_sha: str,
    availability: Dict[str, Any], dl_plan: Dict[str, Any],
    kline_manifest: List, candidate_days: List,
    brute_files: int, brute_mb: float,
) -> None:
    """Write prefilter artifacts when stopping at a prefilter gate."""
    atomic_write_json(output_dir / "preflight.json", {
        "run_id": run_id, "git_sha": git_sha, "precommitment_hash": precommitment_sha,
    })
    atomic_write_json(output_dir / "archive_availability.json", availability)
    atomic_write_json(output_dir / "kline_manifest.json", kline_manifest)
    atomic_write_json(output_dir / "kline_prefilter_summary.json", {
        "total_klines_downloaded": len(kline_manifest),
        "candidate_days_count": len(candidate_days),
        "candidate_days": candidate_days,
    })
    if dl_plan:
        atomic_write_json(output_dir / "download_plan.json", dl_plan)
    atomic_write_json(output_dir / "prefilter_reduction_summary.json", {
        "brute_force_files": brute_files,
        "brute_force_mb": round(brute_mb, 1),
        "planned_files": dl_plan.get("required_file_count", 0) if dl_plan else 0,
        "planned_mb": dl_plan.get("total_estimated_mb", 0) if dl_plan else 0,
        "reduction_ratio": dl_plan.get("reduction_ratio", 1.0) if dl_plan else 1.0,
    })


def _write_prefilter_artifacts(
    output_dir: Path,
    candidate_days: List,
    dl_plan: Dict[str, Any],
    kline_manifest: List,
    all_labels: List,
    brute_files: int,
    brute_mb: float,
    planned_mb: float,
) -> None:
    """Write prefilter success artifacts before evaluation."""
    atomic_write_json(output_dir / "kline_prefilter_summary.json", {
        "total_klines_downloaded": len(kline_manifest),
        "candidate_days_count": len(candidate_days),
        "candidate_days": candidate_days,
        "note": "Kline prefilter is source-only and non-verdict-producing",
    })
    atomic_write_json(output_dir / "candidate_stress_days.json", candidate_days)
    atomic_write_json(output_dir / "download_plan.json", dl_plan)
    atomic_write_json(output_dir / "prefilter_reduction_summary.json", {
        "brute_force_files": brute_files,
        "brute_force_mb": round(brute_mb, 1),
        "planned_files": dl_plan.get("required_file_count", 0),
        "planned_mb": round(planned_mb, 1),
        "reduction_ratio": dl_plan.get("reduction_ratio", 1.0),
    })
    atomic_write_json(output_dir / "aggtrade_exact_stress_reconstruction.json", {
        "total_labels": len(all_labels),
        "note": "All labels are aggTrade-derived (kline was only a prefilter)",
        "symbol_breakdown": {},
    })


if __name__ == "__main__":
    main()
