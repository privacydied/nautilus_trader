#!/usr/bin/env python3
"""Permutation null test CLI — test whether candidate signal groups survive time-shifted nulls.

Usage:
    python -m examples.strategies.venue_agnostic_signal_observer.run_permutation_null \
        --capture-dir data/derivatives_spot_capture_v2_ACTIVE_... \
        --report-dir reports/derivatives_spot_lead_lag_v2_ACTIVE_... \
        --iterations 1000 \
        --seed 42

This script loads an evaluation report, selects null-worthy groups, and runs
permutation null tests by time-shifting source event timestamps and measuring
whether real signals beat the null distribution.

See DERIVATIVES_V2_CAPTURE_RUNBOOK.md Phase 9 for the complete post-capture sequence.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from .permutation_null import (
    select_null_candidate_groups,
    compute_null_distribution,
    DEFAULT_COST_FLOOR_BPS,
    DEFAULT_MIN_EVENTS,
)
from .mcpt_export import _load_jsonl, _load_summary_json, _slugify
from .artifact_metadata import build_metadata, get_metadata, get_metadata_field
from .run_derivatives_spot_lead_lag import load_capture_data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run permutation null tests on candidate signal groups from an evaluation report."
    )
    p.add_argument(
        "--capture-dir",
        type=str,
        required=True,
        help="Path to the capture directory with trades JSONL files.",
    )
    p.add_argument(
        "--report-dir",
        type=str,
        required=True,
        help="Path to the evaluation report directory (containing summary.json, signals.jsonl, forward_returns.jsonl).",
    )
    p.add_argument(
        "--out",
        type=str,
        default="",
        help="Output directory for null test results. Defaults to <report-dir>/null_test/.",
    )
    p.add_argument(
        "--max-groups",
        type=int,
        default=3,
        help="Maximum number of null-worthy groups to test. Default: 3.",
    )
    p.add_argument(
        "--iterations",
        type=int,
        default=1000,
        help="Number of null iterations per group. Default: 1000.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility. Default: 42.",
    )
    p.add_argument(
        "--min-events",
        type=int,
        default=30,
        help="Minimum valid events for a group to be null-tested. Default: 30.",
    )
    p.add_argument(
        "--cost-floor-bps",
        type=float,
        default=50.0,
        help="Assumed all-in cost floor in bps. Default: 50.0.",
    )
    p.add_argument(
        "--shift-mode",
        type=str,
        default="circular_time_shift",
        choices=["circular_time_shift", "block_time_shift"],
        help="Null shift mode. Default: circular_time_shift.",
    )
    p.add_argument(
        "--block-size",
        type=int,
        default=10,
        help="Block size for block_time_shift mode. Default: 10.",
    )
    return p


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fmt_bps(value: object) -> str:
    """Format finite numeric bps values; return N/A for missing/non-finite."""
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value:.2f}"
    return "N/A"


def _fmt_pct(value: object) -> str:
    """Format finite numeric values as percentage; return N/A for missing/non-finite."""
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value * 100:.1f}%"
    return "N/A"


def _group_slug(group: dict[str, Any]) -> str:
    """Build a short slug identifier for a group."""
    src_v = group.get("source_venue", "unknown")
    tgt_v = group.get("target_venue", "unknown")
    sig_type = group.get("signal_type", "unknown")
    lb = group.get("lookback_ms", 0)
    hz = group.get("horizon_ms", 0)
    raw = f"{src_v}_{tgt_v}_{sig_type}_{lb}ms_{hz}ms"
    return _slugify(raw)


def _determine_direction(group: dict[str, Any], matching_signals: list[dict]) -> str:
    """Determine the direction for a group from signals or group metadata.

    Falls back to 'long' if not determinable.
    """
    # Try from group dict
    direction = group.get("direction", "")
    if direction in ("long", "short"):
        return direction

    # Try from signals
    if matching_signals:
        directions = [s.get("direction", "") for s in matching_signals if s.get("direction")]
        if directions:
            # Use the most common direction
            from collections import Counter
            most_common = Counter(directions).most_common(1)[0][0]
            if most_common in ("long", "short"):
                return most_common

    return "long"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    capture_dir = Path(args.capture_dir)

    if not report_dir.exists():
        print(f"ERROR: report directory not found: {report_dir}", file=sys.stderr)
        sys.exit(1)

    if not capture_dir.exists():
        print(f"ERROR: capture directory not found: {capture_dir}", file=sys.stderr)
        sys.exit(1)

    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        print(f"ERROR: summary.json not found in {report_dir}", file=sys.stderr)
        sys.exit(1)

    # ---- 1. Load summary ------------------------------------------------
    with open(summary_path) as f:
        summary = json.load(f)

    all_groups: list[dict[str, Any]] = summary.get("results_by_group", [])
    if not all_groups:
        print("NO_MCPT_WORTHY_GROUPS: no results_by_group in summary")
        # Write skip summary even though there are no groups
        out_dir = Path(args.out) if args.out else report_dir / "null_test"
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = build_metadata(
            capture_mode=get_metadata_field(summary, "capture_mode", ""),
            run_args=args,
        )
        skip_data = {
            "_metadata": meta,
            "skipped": True,
            "skip_reason": "no_results_by_group",
            "groups_tested": 0,
            "results": [],
            "overall_verdict": "NO_MCPT_WORTHY_GROUPS",
        }
        with open(out_dir / "null_test_summary.json", "w") as jf:
            json.dump(skip_data, jf, indent=2, default=str)
        # Minimal MD
        md = [
            "# Permutation Null Test Report",
            "",
            "## Safety",
            "",
            "**Public data observer only. No auth. No orders. No execution.**",
            "",
            "## Result",
            "",
            "NO_MCPT_WORTHY_GROUPS — no groups found to null-test.",
            "",
        ]
        with open(out_dir / "null_test_report.md", "w") as mf:
            mf.write("\n".join(md) + "\n")
        return

    # ---- 2. Get capture_mode from metadata -------------------------------
    meta_obj = get_metadata(summary)
    capture_mode = get_metadata_field(summary, "capture_mode", "")
    if not capture_mode:
        capture_mode = summary.get("capture_mode", "")

    # ---- 3. Select null-worthy groups ------------------------------------
    selected = select_null_candidate_groups(
        all_groups,
        max_groups=args.max_groups,
        cost_floor_bps=args.cost_floor_bps,
        min_events=args.min_events,
    )

    if not selected:
        print("NO_MCPT_WORTHY_GROUPS: no null-worthy groups found")
        out_dir = Path(args.out) if args.out else report_dir / "null_test"
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = build_metadata(capture_mode=capture_mode, run_args=args)
        all_net = [
            g.get("mean_net_bps")
            for g in all_groups
            if g.get("mean_net_bps") is not None and math.isfinite(g["mean_net_bps"])
        ]
        if all_net:
            from statistics import mean as _mean

            avg_net = _mean(all_net)
            reason = (
                f"no_null_worthy_group "
                f"(all {len(all_net)} groups have net returns around {avg_net:.1f} bps "
                f"— cost floor dust)"
            )
        else:
            reason = "no_null_worthy_group (no finite net returns)"

        skip_data = {
            "_metadata": meta,
            "skipped": True,
            "skip_reason": reason,
            "groups_tested": 0,
            "results": [],
            "overall_verdict": "NO_MCPT_WORTHY_GROUPS",
        }
        with open(out_dir / "null_test_summary.json", "w") as jf:
            json.dump(skip_data, jf, indent=2, default=str)
        md = [
            "# Permutation Null Test Report",
            "",
            "## Safety",
            "",
            "**Public data observer only. No auth. No orders. No execution.**",
            "",
            "## Result",
            "",
            f"NO_MCPT_WORTHY_GROUPS — {reason}",
            "",
        ]
        with open(out_dir / "null_test_report.md", "w") as mf:
            mf.write("\n".join(md) + "\n")
        return

    print(f"Selected {len(selected)} null-worthy groups for testing")

    # ---- 4. Load signals and forward_returns once -----------------------
    signals_path = report_dir / "signals.jsonl"
    fwd_path = report_dir / "forward_returns.jsonl"

    all_signals: list[dict] = []
    if signals_path.exists():
        all_signals = _load_jsonl(signals_path)

    all_fwd_returns: list[dict] = []
    if fwd_path.exists():
        all_fwd_returns = _load_jsonl(fwd_path)

    # ---- 5. Load capture data once (all venues/symbols) ------------------
    all_source_venues = list({g.get("source_venue", "") for g in selected})
    all_target_venues = list({g.get("target_venue", "") for g in selected})
    # Collect symbols from groups (both source and target)
    all_symbols_set: set[str] = set()
    for g in selected:
        for sym_key in ("source_symbol", "target_symbol"):
            sym = g.get(sym_key, "")
            if sym:
                all_symbols_set.add(sym)
    all_symbols = sorted(all_symbols_set)

    print(f"Loading capture data: venues={all_source_venues + all_target_venues}, symbols={all_symbols}")
    capture_data = load_capture_data(
        capture_dir,
        source_venues=all_source_venues,
        target_venues=all_target_venues,
        symbols=all_symbols,
    )

    # ---- 6. Fee / slippage defaults from summary ------------------------
    fee_bps = summary.get("fee_bps", 40.0)
    slippage_bps = summary.get("slippage_bps", 5.0)
    quote_mismatch_buffer_bps = summary.get("quote_mismatch_buffer_bps", 5.0)

    # ---- 7. Run null test for each selected group ------------------------
    out_dir = Path(args.out) if args.out else report_dir / "null_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    dist_dir = out_dir / "null_distributions"
    dist_dir.mkdir(parents=True, exist_ok=True)

    group_results: list[dict[str, Any]] = []

    for i, group in enumerate(selected, start=1):
        signal_type = group.get("signal_type", "")
        lookback_ms = int(group.get("lookback_ms", 0))
        horizon_ms = int(group.get("horizon_ms", 0))
        source_venue = group.get("source_venue", "")
        target_venue = group.get("target_venue", "")
        source_symbol = group.get("source_symbol", "")
        target_symbol = group.get("target_symbol", "")

        slug = _group_slug(group)
        print(f"\n  [{i}/{len(selected)}] Testing group: {slug}")

        # 7a. Filter signal events matching the group
        matching_signals = [
            s for s in all_signals
            if s.get("signal_type") == signal_type
            and int(s.get("lookback_ms", 0)) == lookback_ms
        ]

        # Also try matching via metadata.flow_signal_type for compatibility
        if not matching_signals:
            matching_signals = [
                s for s in all_signals
                if (
                    s.get("signal_type") == signal_type
                    or (s.get("metadata") or {}).get("flow_signal_type") == signal_type
                )
                and int((s.get("metadata") or {}).get("lookback_ms", s.get("lookback_ms", 0))) == lookback_ms
            ]

        source_event_timestamps = sorted(int(s["ts_event"]) for s in matching_signals if "ts_event" in s)
        print(f"    Matching signals: {len(matching_signals)}, source timestamps: {len(source_event_timestamps)}")

        if len(source_event_timestamps) < 2:
            print(f"    SKIP: insufficient source event timestamps ({len(source_event_timestamps)})")
            result_entry = {
                "group": group,
                "slug": slug,
                "skipped": True,
                "skip_reason": f"insufficient_source_timestamps: {len(source_event_timestamps)}",
                "candidate_survives_null": False,
            }
            group_results.append(result_entry)
            continue

        # 7b. Get direction
        direction = _determine_direction(group, matching_signals)
        print(f"    Direction: {direction}")

        # 7c. Load target ticks for this group
        # Try exact key match first, then try variations
        target_key = (target_venue, target_symbol)
        target_ticks = capture_data.get(target_key, [])

        # If exact match fails, try with source venue for the target side
        if not target_ticks:
            # Search for any key matching target_venue and target_symbol's asset
            for (v, s), ticks in capture_data.items():
                if v == target_venue and target_symbol in s:
                    target_ticks = ticks
                    break

        if not target_ticks:
            print(f"    SKIP: no target ticks for ({target_venue}, {target_symbol})")
            result_entry = {
                "group": group,
                "slug": slug,
                "skipped": True,
                "skip_reason": f"no_target_ticks: ({target_venue}, {target_symbol})",
                "candidate_survives_null": False,
            }
            group_results.append(result_entry)
            continue

        target_timestamps = [t.ts_event for t in target_ticks]
        target_prices = [t.price for t in target_ticks]
        print(f"    Target ticks: {len(target_ticks)}")

        # 7d. Get quote_mismatch info from group
        has_qm = group.get("has_quote_mismatch", False)
        # Also check from summary-level or symbols
        if not has_qm and source_symbol and target_symbol:
            try:
                from .symbol_aliases import quote_mismatch as sym_qm
                has_qm = sym_qm(source_symbol, target_symbol)
            except (ValueError, ImportError):
                pass

        # 7e. Get real statistics from the group
        real_event_count = int(group.get("valid_count", 0))
        real_mean_net_bps = group.get("mean_net_bps")
        real_median_net_bps = group.get("median_net_bps")
        real_win_rate = group.get("win_rate")

        # Coerce None / non-finite
        if real_mean_net_bps is None or not math.isfinite(real_mean_net_bps):
            real_mean_net_bps = float("nan")
        if real_median_net_bps is None or not math.isfinite(real_median_net_bps):
            real_median_net_bps = float("nan")
        if real_win_rate is None or not math.isfinite(real_win_rate):
            real_win_rate = float("nan")

        # 7f. Run null distribution
        print(f"    Running {args.iterations} iterations (shift_mode={args.shift_mode})...")
        null_dist = compute_null_distribution(
            source_event_timestamps=source_event_timestamps,
            target_timestamps=target_timestamps,
            target_prices=target_prices,
            direction=direction,
            horizons_ms=[horizon_ms],
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            quote_mismatch=has_qm,
            iterations=args.iterations,
            seed=args.seed,
            shift_mode=args.shift_mode,
            block_size=args.block_size,
        )

        # ---- Compute survival check ----
        null_means = null_dist.get("null_mean_net_bps", [])
        null_win_rates = null_dist.get("null_win_rates", [])
        pctls = null_dist.get("percentiles", {})

        valid_null_means = [x for x in null_means if math.isfinite(x)]
        valid_null_win_rates = [x for x in null_win_rates if math.isfinite(x)]

        # Empirical p-value
        if math.isfinite(real_mean_net_bps) and len(valid_null_means) > 0:
            count_ge = sum(1 for nm in valid_null_means if nm >= real_mean_net_bps)
            p_value = count_ge / len(valid_null_means)
        else:
            p_value = 1.0

        null_mean_p95 = pctls.get("mean_net_bps_p95", float("nan"))
        null_wr_p50 = pctls.get("win_rate_p50", float("nan"))

        # Survival criteria (all must pass)
        criterion_mean_positive = math.isfinite(real_mean_net_bps) and real_mean_net_bps > 0
        criterion_enough_events = real_event_count >= args.min_events
        criterion_beats_p95 = (
            math.isfinite(real_mean_net_bps)
            and math.isfinite(null_mean_p95)
            and real_mean_net_bps > null_mean_p95
        )
        criterion_p_value = p_value <= 0.05
        criterion_win_rate = (
            math.isfinite(real_win_rate)
            and math.isfinite(null_wr_p50)
            and real_win_rate > null_wr_p50
        )

        all_pass = (
            criterion_mean_positive
            and criterion_enough_events
            and criterion_beats_p95
            and criterion_p_value
            and criterion_win_rate
        )

        if all_pass:
            verdict = "SURVIVED_NULL_TEST"
        else:
            reasons: list[str] = []
            if not criterion_mean_positive:
                reasons.append(f"mean_net_bps_not_positive: {_fmt_bps(real_mean_net_bps)}")
            if not criterion_enough_events:
                reasons.append(f"insufficient_events: {real_event_count} < {args.min_events}")
            if not criterion_beats_p95:
                reasons.append(
                    f"real_mean_not_above_null_p95: {_fmt_bps(real_mean_net_bps)} vs {_fmt_bps(null_mean_p95)}"
                )
            if not criterion_p_value:
                reasons.append(f"p_value_above_0.05: {p_value:.4f}")
            if not criterion_win_rate:
                reasons.append(
                    f"win_rate_not_above_null_median: {_fmt_pct(real_win_rate)} vs {_fmt_pct(null_wr_p50)}"
                )
            verdict = "NULL_REJECTED_DIAGNOSTIC: " + "; ".join(reasons)

        real_beats_null = (
            math.isfinite(real_mean_net_bps)
            and len(valid_null_means) > 0
            and real_mean_net_bps > null_mean_p95
        )

        # Build per-group result dict
        result_entry: dict[str, Any] = {
            "group": {
                "source_venue": source_venue,
                "target_venue": target_venue,
                "source_symbol": source_symbol,
                "target_symbol": target_symbol,
                "signal_type": signal_type,
                "lookback_ms": lookback_ms,
                "horizon_ms": horizon_ms,
            },
            "slug": slug,
            "skipped": False,
            "real_event_count": real_event_count,
            "real_mean_net_bps": real_mean_net_bps,
            "real_median_net_bps": real_median_net_bps,
            "real_win_rate": real_win_rate,
            "null_iterations": args.iterations,
            "null_seed": args.seed,
            "shift_mode": args.shift_mode,
            "block_size": args.block_size,
            "null_mean_net_bps_p50": pctls.get("mean_net_bps_p50", float("nan")),
            "null_mean_net_bps_p95": pctls.get("mean_net_bps_p95", float("nan")),
            "null_mean_net_bps_p99": pctls.get("mean_net_bps_p99", float("nan")),
            "null_win_rate_p50": pctls.get("win_rate_p50", float("nan")),
            "empirical_p_value": p_value,
            "real_beats_null": real_beats_null,
            "candidate_survives_null": all_pass,
            "verdict": verdict,
            "direction": direction,
            "cost_floor_bps": args.cost_floor_bps,
            "min_events": args.min_events,
            "source_timestamp_count": len(source_event_timestamps),
            "target_tick_count": len(target_ticks),
        }

        group_results.append(result_entry)
        print(f"    Verdict: {verdict}")
        print(f"    Real mean: {_fmt_bps(real_mean_net_bps)} bps | Null p95: {_fmt_bps(null_mean_p95)} bps | p-value: {p_value:.4f}")

        # 7h. Save per-group null distribution
        dist_path = dist_dir / f"{slug}.json"
        with open(dist_path, "w") as df:
            json.dump(null_dist, df, indent=2, default=str)
        print(f"    Saved distribution: {dist_path.name}")

    # ---- 8. Write outputs ------------------------------------------------
    meta = build_metadata(capture_mode=capture_mode, run_args=args)

    # Determine overall verdict
    survived = [r for r in group_results if r.get("candidate_survives_null") is True]
    if survived:
        overall_verdict = "SURVIVED_NULL_TEST"
    elif any(r.get("skipped") for r in group_results):
        overall_verdict = "PARTIAL_SKIP"
    else:
        overall_verdict = "NULL_REJECTED_DIAGNOSTIC"

    summary_data: dict[str, Any] = {
        "_metadata": meta,
        "capture_dir": str(capture_dir),
        "report_dir": str(report_dir),
        "capture_mode": capture_mode,
        "groups_tested": len(group_results),
        "groups_survived": len(survived),
        "overall_verdict": overall_verdict,
        "shift_mode": args.shift_mode,
        "iterations": args.iterations,
        "seed": args.seed,
        "block_size": args.block_size,
        "cost_floor_bps": args.cost_floor_bps,
        "min_events": args.min_events,
        "results": group_results,
    }

    summary_json_path = out_dir / "null_test_summary.json"
    with open(summary_json_path, "w") as sf:
        json.dump(summary_data, sf, indent=2, default=str)

    # ---- 9. Write markdown report ----------------------------------------
    md_lines: list[str] = [
        "# Permutation Null Test Report",
        "",
        "## Safety",
        "",
        "**Public data observer only. No auth. No orders. No execution.**",
        "",
        "## Configuration",
        "",
        f"- Capture dir: `{capture_dir}`",
        f"- Report dir: `{report_dir}`",
        f"- Capture mode: {capture_mode or 'unspecified'}",
        f"- Shift mode: {args.shift_mode}",
        f"- Iterations: {args.iterations}",
        f"- Seed: {args.seed}",
        f"- Block size: {args.block_size}",
        f"- Cost floor: {args.cost_floor_bps} bps",
        f"- Min events: {args.min_events}",
        "",
        "## Groups Tested",
        "",
        f"Selected **{len(selected)}** null-worthy groups (of {len(all_groups)} total).",
        "",
    ]

    if not group_results:
        md_lines.append("No groups were tested.\n")
    else:
        # Table header
        md_lines.extend([
            "| # | Slug | Signal | Lookback | Horizon | Survived | Real Mean | Null p95 | p-value |",
            "|---|------|--------|----------|---------|----------|-----------|----------|---------|",
        ])

        for idx, r in enumerate(group_results, start=1):
            if r.get("skipped"):
                md_lines.append(
                    f"| {idx} | {r.get('slug', '?')} | — | — | — | SKIPPED | — | — | — |"
                )
                continue

            g = r.get("group", {})
            survive_str = "✅ Yes" if r.get("candidate_survives_null") else "❌ No"
            md_lines.append(
                f"| {idx} | {r.get('slug', '?')} "
                f"| {g.get('signal_type', '?')} "
                f"| {g.get('lookback_ms', '?')}ms "
                f"| {g.get('horizon_ms', '?')}ms "
                f"| {survive_str} "
                f"| {_fmt_bps(r.get('real_mean_net_bps'))} bps "
                f"| {_fmt_bps(r.get('null_mean_net_bps_p95'))} bps "
                f"| {r.get('empirical_p_value', 'N/A'):.4f} |"
            )

    md_lines.append("")
    md_lines.append("## Survival Verdict")
    md_lines.append("")
    for r in group_results:
        if r.get("skipped"):
            md_lines.append(f"- **{r.get('slug', '?')}**: SKIPPED — {r.get('skip_reason', 'unknown')}")
        elif r.get("candidate_survives_null"):
            md_lines.append(f"- **{r.get('slug', '?')}**: ✅ SURVIVED_NULL_TEST")
        else:
            md_lines.append(f"- **{r.get('slug', '?')}**: ❌ {r.get('verdict', 'unknown')}")

    md_lines.append("")
    md_lines.append("## Overall Verdict")
    md_lines.append("")
    md_lines.append(f"**{overall_verdict}**")
    md_lines.append("")

    # Important disclaimer
    md_lines.append("## Important Note")
    md_lines.append("")
    md_lines.append(
        "**NULL_REJECTED_DIAGNOSTIC is diagnostic evidence only.** "
        "A null-rejected verdict means the observed signal return is "
        "statistically unlikely under the time-shifted null distribution. "
        "This is not investment advice, not an execution signal, and does not "
        "imply profitability after real-world execution costs, latency, or market impact."
    )
    md_lines.append("")

    md_path = out_dir / "null_test_report.md"
    with open(md_path, "w") as mf:
        mf.write("\n".join(md_lines) + "\n")

    # ---- 10. Final summary output ----------------------------------------
    print()
    print("=" * 60)
    print("NULL TEST COMPLETE")
    print(f"  Report: {report_dir}")
    print(f"  Capture mode: {capture_mode or 'unspecified'}")
    print(f"  Groups tested: {len(group_results)}/{len(all_groups)}")
    print(f"  Groups survived: {len(survived)}")
    print(f"  Overall verdict: {overall_verdict}")
    print(f"  Output dir: {out_dir}")
    print(f"  Summary: {summary_json_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()