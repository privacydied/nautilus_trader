#!/usr/bin/env python3
"""
Source-only exact stress population sizing for cross-asset beta-lag archive v0.

This is NOT a hypothesis evaluation.
This is NOT a rejection.
This must NOT update REJECTED_RESEARCH.md.

Downloads/reads only BTCUSDT and ETHUSDT aggTrades across the full
precommitted calendar, reconstructs exact stress labels, and estimates
what target download volume would be needed for full evaluation.

Usage:
    python3.14 -m examples.strategies.venue_agnostic_signal_observer. \
        run_cross_asset_beta_lag_archive_source_sizing \
        --precommitment <path> --out <dir> \
        --seed 42 --max-source-download-gb 10 --max-full-eval-download-gb 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(_THIS_DIR))

from .binance_vision_archive import _iter_date_range
from .binance_vision_archive import download_daily_agg_trades
from .binance_vision_archive import estimate_file_size_mb
from .binance_vision_archive import parse_agg_trade_csv
from .cross_asset_beta_lag_archive import CALENDAR_END
from .cross_asset_beta_lag_archive import CALENDAR_START
from .cross_asset_beta_lag_archive import SOURCE_SYMBOLS
from .cross_asset_beta_lag_archive import STRESS_RULES
from .cross_asset_beta_lag_archive import TARGET_SYMBOLS
from .cross_asset_beta_lag_archive import VENUE
from .cross_asset_beta_lag_archive import StressLabel
from .cross_asset_beta_lag_archive import _get_git_sha
from .cross_asset_beta_lag_archive import _now_utc_iso
from .cross_asset_beta_lag_archive import _sha256_json
from .cross_asset_beta_lag_archive import assign_independent_windows
from .cross_asset_beta_lag_archive import deduplicate_labels
from .cross_asset_beta_lag_archive import generate_stress_labels
from .run_artifacts import atomic_write_json
from .run_artifacts import atomic_write_jsonl
from .run_artifacts import atomic_write_text
from .run_artifacts import create_run_id
from .tick_models import TradeTickLite


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MS_TO_NS = 1_000_000


def _harmonic_sum(n: int) -> float:
    s = 0.0
    for j in range(1, n + 1):
        s += 1.0 / j
    return s


def _date_from_ns(ts_ns: int) -> str:
    ts_sec = ts_ns // 1_000_000_000
    dt = datetime.fromtimestamp(ts_sec, tz=UTC)
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Source exact stress population sizing for cross-asset beta-lag archive v0"
    )
    parser.add_argument("--precommitment", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-source-download-gb", type=float, default=10.0)
    parser.add_argument("--max-full-eval-download-gb", type=float, default=10.0)
    args = parser.parse_args()

    git_sha = _get_git_sha()
    print("=== Cross-Asset Beta-Lag Archive v0 — Source Exact Stress Sizing ===")
    print(f"Git SHA: {git_sha}")
    print(f"Start: {_now_utc_iso()}")
    print(f"Source: {SOURCE_SYMBOLS}")
    print(f"Targets: {TARGET_SYMBOLS}")
    print(f"Calendar: {CALENDAR_START} to {CALENDAR_END}")
    print(f"Max source GB: {args.max_source_download_gb}")
    print(f"Max full eval GB: {args.max_full_eval_download_gb}")
    print("====================================================================\n")

    run_id = create_run_id("source_sizing")
    output_dir = Path(args.out) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 0: Validate precommitment
    print("[Phase 0] Validating precommitment...")
    precommitment_path = Path(args.precommitment)
    if not precommitment_path.exists():
        print(f"ERROR: Precommitment not found: {args.precommitment}")
        _write_summary(output_dir, run_id, git_sha, "", "PRECOMMITMENT_NOT_FOUND",
                       {"early_stop_reason": "precommitment_not_found"})
        return
    precommitment_data = json.loads(precommitment_path.read_text("utf-8"))
    precommitment_sha = _sha256_json(precommitment_data)
    print(f"  Hash: {precommitment_sha}")

    # Phase 1: Compute date range
    date_list = _iter_date_range(CALENDAR_START, CALENDAR_END)
    total_days = len(date_list)
    print(f"\n[Phase 1] Calendar: {total_days} days")

    # Estimate source volume
    source_files_total = len(SOURCE_SYMBOLS) * total_days
    source_mb_est = sum(estimate_file_size_mb(sym) for sym in SOURCE_SYMBOLS) * total_days
    source_gb_est = source_mb_est / 1024
    print(f"  Source files: {source_files_total} (~{source_gb_est:.1f} GB)")

    if source_gb_est > args.max_source_download_gb:
        print(f"  SOURCE VOLUME EXCEEDS CAP: {source_gb_est:.1f} > {args.max_source_download_gb} GB")
        _write_preflight(output_dir, run_id, git_sha, precommitment_sha)
        _write_summary(output_dir, run_id, git_sha, precommitment_sha,
                       "SOURCE_EXACT_STRESS_SIZING_VOLUME_TOO_LARGE",
                       {"source_gb_est": round(source_gb_est, 1),
                        "max_source_gb": args.max_source_download_gb,
                        "early_stop_reason": f"source_volume_{source_gb_est:.1f}gb_exceeds_{args.max_source_download_gb}gb"})
        _write_source_summary(output_dir, {
            "source_symbols": SOURCE_SYMBOLS,
            "total_days": total_days,
            "source_files_estimated": source_files_total,
            "source_mb_estimated": round(source_mb_est, 1),
            "source_gb_estimated": round(source_gb_est, 1),
            "max_source_gb": args.max_source_download_gb,
            "status": "VOLUME_GATE_STOPPED",
        })
        print("\n=== VERDICT: SOURCE_EXACT_STRESS_SIZING_VOLUME_TOO_LARGE ===")
        return

    # Phase 2: Download source aggTrades
    print("\n[Phase 2] Downloading source aggTrades...")
    source_ticks: Dict[str, List[TradeTickLite]] = {}
    source_manifest: List[Dict[str, Any]] = []
    total_bytes = 0
    total_files = 0
    failed_files = 0

    for src_sym in SOURCE_SYMBOLS:
        print(f"  {src_sym}...")
        sym_ticks: List[TradeTickLite] = []
        for d in date_list:
            data, sha = download_daily_agg_trades(src_sym, d, cache=True)
            if data is None:
                failed_files += 1
                source_manifest.append({
                    "symbol": src_sym, "date": d, "source": "aggTrades",
                    "status": "not_found", "sha256": None, "size_bytes": None, "row_count": 0,
                })
                continue
            ticks = parse_agg_trade_csv(data, src_sym, venue=VENUE)
            sym_ticks.extend(ticks)
            total_bytes += len(data)
            source_manifest.append({
                "symbol": src_sym, "date": d, "source": "aggTrades",
                "status": "downloaded", "sha256": sha,
                "size_bytes": len(data), "row_count": len(ticks),
            })
            total_files += 1
        source_ticks[src_sym] = sym_ticks
        print(f"    {len(sym_ticks)} ticks")

    total_mb = total_bytes / (1024 * 1024)
    print(f"\n  Downloaded: {total_files} files, {total_mb:.1f} MB, {failed_files} failed")
    print(f"  Total source ticks: {sum(len(t) for t in source_ticks.values())}")

    # Phase 3: Reconstruct exact stress labels
    print("\n[Phase 3] Reconstructing exact stress labels from aggTrades...")
    all_labels: List[StressLabel] = []

    for src_sym in SOURCE_SYMBOLS:
        ticks = source_ticks.get(src_sym, [])
        if not ticks:
            print(f"  WARNING: No ticks for {src_sym}")
            continue
        for rule_name, lookback_sec, threshold in STRESS_RULES:
            labels = generate_stress_labels(ticks, src_sym,
                                            lookback_seconds=lookback_sec,
                                            threshold_bps=threshold)
            all_labels.extend(labels)
            print(f"  {src_sym} {rule_name}: {len(labels)} labels")

    if not all_labels:
        verdict = "NEEDS_MORE_DATA_ARCHIVE_NO_EXACT_SOURCE_STRESS"
        _write_preflight(output_dir, run_id, git_sha, precommitment_sha)
        _write_source_summary(output_dir, {
            "source_symbols": SOURCE_SYMBOLS,
            "total_days": total_days,
            "source_files_downloaded": total_files,
            "source_mb_downloaded": round(total_mb, 1),
            "status": "ZERO_EXACT_STRESS_LABELS",
        })
        _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict,
                       {"early_stop_reason": "zero_exact_source_stress_labels"})
        print(f"\n=== VERDICT: {verdict} ===")
        return

    # Dedup and assign independent windows
    deduped = deduplicate_labels(all_labels)
    windowed = assign_independent_windows(deduped)
    independent_window_ids = sorted({l.independent_window_id for l in windowed})

    # Count by source/direction/rule
    by_rule: Dict[str, int] = Counter()
    by_source_dir: Dict[str, int] = Counter()
    stress_days: set = set()

    for lbl in windowed:
        rule = lbl.rule_name
        by_rule[rule] += 1
        key = f"{lbl.source_symbol}_{lbl.direction}"
        by_source_dir[key] += 1
        day = _date_from_ns(lbl.stress_end_ns)
        stress_days.add(day)

    # Independent windows per month
    windows_by_month: Counter = Counter()
    for wid in independent_window_ids:
        month = wid.split("_")[1][:4] + "-" + wid.split("_")[1][4:6] if len(wid.split("_")) > 1 else "unknown"
        windows_by_month[month] += 1

    stress_days_sorted = sorted(stress_days)

    print(f"\n  Exact stress labels (after dedup/windowing): {len(windowed)}")
    print(f"  Independent windows: {len(independent_window_ids)}")
    print(f"  Stress days: {len(stress_days_sorted)}")
    print(f"  By rule: {dict(by_rule)}")
    print(f"  By source/direction: {dict(by_source_dir)}")

    # Phase 4: Estimate target download plan
    print("\n[Phase 4] Estimating target download plan...")
    # For each stress day, estimate target files needed
    # Each stress day: 4 target symbols × 1 file each = 4 files
    # Plus next-day buffer for day-end windows: +4 files
    # Plus prev-day source context:  +2 source files (already have source)
    target_days_set: set = set()
    for day in stress_days_sorted:
        target_days_set.add(day)
        # Next-day buffer for forward coverage
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
        next_dt = dt + timedelta(days=1)
        next_day = next_dt.strftime("%Y-%m-%d")
        if next_day in date_list:
            target_days_set.add(next_day)

    target_days = sorted(target_days_set)
    target_files_est = len(TARGET_SYMBOLS) * len(target_days)
    target_mb_est = sum(estimate_file_size_mb(sym) for sym in TARGET_SYMBOLS) * len(target_days)
    target_gb_est = target_mb_est / 1024

    total_full_eval_files = total_files + target_files_est
    total_full_eval_mb = total_mb + target_mb_est
    total_full_eval_gb = total_full_eval_mb / 1024

    print(f"  Target days (incl buffer): {len(target_days)}")
    print(f"  Target files: {target_files_est} ({target_gb_est:.1f} GB)")
    print(f"  Total full eval: {total_full_eval_files} files ({total_full_eval_gb:.1f} GB)")

    # Caps summary
    caps = {"5 GB": 5 * 1024, "10 GB": 10 * 1024, "20 GB": 20 * 1024, "50 GB": 50 * 1024}
    caps_practical = {}
    for label, cap_mb in caps.items():
        caps_practical[label] = total_full_eval_mb <= cap_mb

    # Verdict
    if total_full_eval_mb <= args.max_full_eval_download_gb * 1024:
        verdict = "ARCHIVE_EVALUATION_PRACTICAL_UNDER_CAP"
    else:
        verdict = "ARCHIVE_EVALUATION_NOT_PRACTICAL_UNDER_DAILY_ZIP_GRANULARITY"

    print(f"\n  Full eval practical under caps: {caps_practical}")
    print(f"  Verdict: {verdict}")

    # Phase 5: Write artifacts
    print("\n[Phase 5] Writing artifacts...")

    # preflight.json
    atomic_write_json(output_dir / "preflight.json", {
        "run_id": run_id,
        "study_id": "cross_asset_beta_lag_archive_v0_source_sizing",
        "safety_mode": "public_data_observer_only",
        "git_sha": git_sha,
        "precommitment_hash": precommitment_sha,
        "generated_at": _now_utc_iso(),
    })

    # source_download_manifest.json (compact)
    atomic_write_json(output_dir / "source_download_manifest.json", {
        "total_source_files": total_files,
        "failed_source_files": failed_files,
        "total_source_bytes": total_bytes,
        "total_source_mb": round(total_mb, 1),
        "per_symbol_files": {sym: len(source_ticks.get(sym, [])) for sym in SOURCE_SYMBOLS},
        "per_symbol_bytes": {sym: sum(
            m.get("size_bytes", 0) or 0 for m in source_manifest if m["symbol"] == sym
        ) for sym in SOURCE_SYMBOLS},
    })

    # source_archive_file_manifest.json (SHA256 summary)
    atomic_write_json(output_dir / "source_archive_file_manifest.json", source_manifest[:100])  # first 100 rows for metadata

    # exact_source_stress_labels.jsonl
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
            "rule_name": lbl.rule_name,
            "independent_window_id": lbl.independent_window_id,
            "day": _date_from_ns(lbl.stress_end_ns),
        })
    atomic_write_jsonl(output_dir / "exact_source_stress_labels.jsonl", label_rows)

    # exact_source_independent_windows.jsonl
    window_rows = [{"window_id": wid, "month": wid.split("_")[1][:6] if "_" in wid and len(wid.split("_")[1]) >= 6 else ""}
                   for wid in independent_window_ids]
    atomic_write_jsonl(output_dir / "exact_source_independent_windows.jsonl", window_rows)

    # source_exact_population_summary.json
    source_summary = {
        "study_id": "cross_asset_beta_lag_archive_v0_source_sizing",
        "run_id": run_id,
        "precommitment_hash": precommitment_sha,
        "git_sha": git_sha,
        "completion_time": _now_utc_iso(),
        "full_calendar_start": CALENDAR_START,
        "full_calendar_end": CALENDAR_END,
        "total_calendar_days": total_days,
        "source_symbols": SOURCE_SYMBOLS,
        "source_files_required": source_files_total,
        "source_files_downloaded": total_files,
        "source_bytes_downloaded": total_bytes,
        "source_mb_downloaded": round(total_mb, 1),
        "total_exact_labels_raw": len(all_labels),
        "total_exact_labels_dedup_windowed": len(windowed),
        "exact_30s_labels": int(by_rule.get("30s_30bps", 0)),
        "exact_60s_labels": int(by_rule.get("60s_50bps", 0)),
        "by_source_direction": dict(by_source_dir),
        "exact_stress_days_count": len(stress_days_sorted),
        "exact_stress_days": stress_days_sorted,
        "exact_independent_windows_count": len(independent_window_ids),
        "independent_windows_per_month": dict(sorted(windows_by_month.items())),
        "target_symbols": TARGET_SYMBOLS,
        "target_days_required": len(target_days),
        "target_files_estimated": target_files_est,
        "target_mb_estimated": round(target_mb_est, 1),
        "target_gb_estimated": round(target_gb_est, 1),
        "total_full_eval_files_estimated": total_full_eval_files,
        "total_full_eval_mb_estimated": round(total_full_eval_mb, 1),
        "total_full_eval_gb_estimated": round(total_full_eval_gb, 1),
        "full_eval_practical_under_caps": caps_practical,
        "max_source_download_gb": args.max_source_download_gb,
        "max_full_eval_download_gb": args.max_full_eval_download_gb,
        "status": verdict,
    }
    atomic_write_json(output_dir / "source_exact_population_summary.json", source_summary)

    # target_download_plan_estimate.json
    target_plan = {
        "target_symbols": TARGET_SYMBOLS,
        "unique_target_days": len(target_days),
        "target_days": target_days,
        "est_files_per_sym": len(target_days),
        "est_total_target_files": target_files_est,
        "est_total_target_mb": round(target_mb_est, 1),
        "est_total_target_gb": round(target_gb_est, 1),
        "note": "Target files estimated from exact source stress days + 1-day forward buffer. No target files were downloaded.",
    }
    atomic_write_json(output_dir / "target_download_plan_estimate.json", target_plan)

    # FINAL_REPORT.md
    report_lines = [
        "# Cross-Asset Beta-Lag Archive v0 — Source Exact Stress Sizing",
        "",
        f"**Run ID:** {run_id}",
        "**Study:** cross_asset_beta_lag_archive_v0_source_sizing",
        "**Branch:** feat/cross-asset-beta-lag-archive-v0",
        f"**Starting SHA:** {git_sha}",
        "**Safety posture:** public_data_observer_only",
        f"**Precommitment SHA:** {precommitment_sha}",
        "",
        "## Calendar",
        f"- Full calendar retained: {CALENDAR_START} to {CALENDAR_END}",
        f"- Total calendar days: {total_days}",
        "",
        "## Source Download",
        f"- Source symbols: {SOURCE_SYMBOLS}",
        f"- Source files required: {source_files_total}",
        f"- Source files downloaded: {total_files}",
        f"- Source MB downloaded: {total_mb:.1f}",
        f"- Failed downloads: {failed_files}",
        "",
        "## Exact Stress Labels (from aggTrades only)",
        f"- Total raw stress labels: {len(all_labels)}",
        f"- After dedup/windowing: {len(windowed)}",
        f"- 30s labels: {by_rule.get('30s_30bps', 0)}",
        f"- 60s labels: {by_rule.get('60s_50bps', 0)}",
        f"- By source/direction: {dict(by_source_dir)}",
        f"- Unique stress days: {len(stress_days_sorted)}",
        f"- Independent windows: {len(independent_window_ids)}",
        f"- Windows per month: {dict(sorted(windows_by_month.items()))}",
        "",
        "## Target Plan Estimate",
        f"- Target symbols: {TARGET_SYMBOLS}",
        f"- Target days required: {len(target_days)}",
        f"- Estimated target files: {target_files_est}",
        f"- Estimated target MB: {target_mb_est:.1f}",
        f"- Estimated target GB: {target_gb_est:.1f}",
        "",
        "## Full Evaluation Cost",
        f"- Total files: {total_full_eval_files}",
        f"- Total MB: {total_full_eval_mb:.1f}",
        f"- Total GB: {total_full_eval_gb:.1f}",
        f"- Practical under 5 GB cap: {caps_practical.get('5 GB', False)}",
        f"- Practical under 10 GB cap: {caps_practical.get('10 GB', False)}",
        f"- Practical under 20 GB cap: {caps_practical.get('20 GB', False)}",
        f"- Practical under 50 GB cap: {caps_practical.get('50 GB', False)}",
        "",
        "## Verdict",
        f"**{verdict}**",
        "",
        "## What This Is Not",
        "- Not a hypothesis evaluation",
        "- Not a rejection",
        "- REJECTED_RESEARCH.md was NOT updated",
        "- No target symbols were downloaded",
        "- No forward returns, null, FDR, holdout were computed",
        "- No thresholds, horizons, costs, or verdict gates were changed",
    ]
    atomic_write_text(output_dir / "FINAL_REPORT.md", "\n".join(report_lines))

    # Summary
    summary = {
        "run_id": run_id,
        "study_id": "cross_asset_beta_lag_archive_v0_source_sizing",
        "precommitment_hash": precommitment_sha,
        "git_sha": git_sha,
        "final_verdict": verdict,
        "source_mb_downloaded": round(total_mb, 1),
        "exact_stress_labels": len(windowed),
        "exact_independent_windows": len(independent_window_ids),
        "exact_stress_days": len(stress_days_sorted),
        "estimated_target_gb": round(target_gb_est, 1),
        "total_full_eval_gb": round(total_full_eval_gb, 1),
        "practical_under_caps": caps_practical,
        "registry_updated": False,
        "completion_time": _now_utc_iso(),
    }
    _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict, summary)

    print(f"\n=== DONE: {verdict} ===")
    print(f"Report: {output_dir}")


def _write_preflight(output_dir, run_id, git_sha, precommitment_sha):
    atomic_write_json(output_dir / "preflight.json", {
        "run_id": run_id,
        "git_sha": git_sha,
        "precommitment_hash": precommitment_sha,
    })


def _write_source_summary(output_dir, data):
    atomic_write_json(output_dir / "source_exact_population_summary.json", data)
    atomic_write_json(output_dir / "target_download_plan_estimate.json", {
        "note": "Not computed — source sizing did not complete.",
    })
    atomic_write_text(output_dir / "FINAL_REPORT.md",
                      f"# Cross-Asset Beta-Lag Archive v0 — Source Sizing\n\n"
                      f"**Status:** {data.get('status', 'unknown')}\n\n"
                      f"Source download {data.get('status', '')}.\n")


def _write_summary(output_dir, run_id, git_sha, precommitment_sha, verdict, extra=None):
    s = {
        "run_id": run_id,
        "study_id": "cross_asset_beta_lag_archive_v0_source_sizing",
        "precommitment_hash": precommitment_sha,
        "git_sha": git_sha,
        "final_verdict": verdict,
        "completion_time": _now_utc_iso(),
    }
    if extra:
        s.update(extra)
    atomic_write_json(output_dir / "summary.json", s)


if __name__ == "__main__":
    main()
