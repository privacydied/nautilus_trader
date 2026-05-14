"""CLI runner for cost-sensitivity / breakeven diagnostic.

Reads an existing derivatives spot lead-lag evaluation report and writes
a cost-sensitivity analysis showing what cost level each group would need
to break even. This is diagnostic-only and cannot create candidates or
change verdicts.

Usage:
    python -m examples.strategies.venue_agnostic_signal_observer.run_cost_sensitivity \
        --report-dir reports/.../heatmap_focused_eval \
        --out reports/.../heatmap_focused_eval/cost_sensitivity \
        --cost-levels-bps 50,10,5,1,0.5 \
        --min-events 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from venue_agnostic_signal_observer.cost_sensitivity import (
    SAFETY_MODE,
    compute_cost_sensitivity,
    load_report_groups,
    write_cost_sensitivity_reports,
    DEFAULT_COST_LEVELS_BPS,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cost-sensitivity / breakeven diagnostic for derivatives spot lead-lag reports.",
    )
    parser.add_argument(
        "--report-dir",
        required=True,
        help="Path to existing derivatives spot lead-lag report directory.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory for cost-sensitivity reports.",
    )
    parser.add_argument(
        "--cost-levels-bps",
        default="50,10,5,1,0.5",
        help="Comma-separated cost levels in bps for margin computation. "
             "Default: 50,10,5,1,0.5",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=0,
        help="Minimum valid_count to include a group. Default: 0 (include all).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    if not report_dir.exists():
        print(f"[ERROR] Report directory not found: {report_dir}")
        sys.exit(1)

    # Parse cost levels
    cost_levels = [float(x.strip()) for x in args.cost_levels_bps.split(",")]

    print("=" * 70)
    print("COST-SENSITIVITY / BREAKEVEN DIAGNOSTIC")
    print("=" * 70)
    print(f"\n[1] Loading report from: {report_dir}")

    groups, meta = load_report_groups(report_dir)
    if not groups:
        print("  No evaluated groups found. Exiting.")
        print(f"\nVERDICT: NO_EVALUATED_GROUPS")
        # Still write empty report
        summary = compute_cost_sensitivity(
            groups=[],
            all_in_cost_bps=meta.get("all_in_cost_bps", 50.0),
            fee_bps=meta.get("fee_bps", 40.0),
            slippage_bps=meta.get("slippage_bps", 5.0),
            quote_mismatch_buffer_bps=meta.get("quote_mismatch_buffer_bps", 5.0),
            cost_levels_bps=cost_levels,
            min_events=args.min_events,
        )
        summary.report_dir = str(report_dir)
        write_cost_sensitivity_reports(summary, args.out)
        print(f"  Reports: {args.out}")
        return

    all_in_cost = meta.get("all_in_cost_bps", 50.0)
    print(f"  Groups: {len(groups)}")
    print(f"  All-in cost: {all_in_cost} bps")
    print(f"  Cost levels: {cost_levels}")
    print(f"  Min events filter: {args.min_events}")

    print(f"\n[2] Computing cost-sensitivity analysis...")
    summary = compute_cost_sensitivity(
        groups=groups,
        all_in_cost_bps=all_in_cost,
        fee_bps=meta.get("fee_bps", 40.0),
        slippage_bps=meta.get("slippage_bps", 5.0),
        quote_mismatch_buffer_bps=meta.get("quote_mismatch_buffer_bps", 5.0),
        cost_levels_bps=cost_levels,
        min_events=args.min_events,
    )
    summary.report_dir = str(report_dir)

    print(f"\n  Verdict: {summary.verdict}")
    print(f"  Total groups: {summary.total_groups}")
    print(f"  Finite groups: {summary.finite_groups}")
    print(f"  Reason: {summary.reason}")

    # Show top 5 rows
    if summary.rows:
        print(f"\n  Top 5 groups by breakeven cost (raw edge):")
        for i, row in enumerate(summary.rows[:5], 1):
            raw = row.get("mean_raw_bps")
            be = row.get("breakeven_cost_bps")
            net = row.get("mean_net_bps")
            vc = row.get("valid_count", 0)
            sig = row.get("signal_type", "")
            lb = row.get("lookback_ms", 0)
            hz = row.get("horizon_ms", 0)
            src = row.get("source_venue", "")
            tgt = row.get("target_venue", "")
            print(f"    {i}. {src}→{tgt} {sig}/{lb}ms h={hz}ms "
                  f"raw={raw:.3f} net={net:.3f} breakeven={be:.3f} n={vc}")

    # Viability summary at each cost level
    print(f"\n  Viability at each cost level:")
    for level in cost_levels:
        viable = sum(1 for r in summary.rows if r.get("mean_raw_bps") is not None and r["mean_raw_bps"] > level)
        print(f"    {level} bps: {viable} groups viable")

    print(f"\n[3] Writing reports to: {args.out}")
    write_cost_sensitivity_reports(summary, args.out)

    print(f"\nVERDICT: {summary.verdict}")
    print(f"  Reports: {args.out}/")


if __name__ == "__main__":
    main()