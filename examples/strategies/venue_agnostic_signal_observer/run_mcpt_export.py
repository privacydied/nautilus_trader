#!/usr/bin/env python3
"""MCPT export CLI — decide whether MCPT is warranted and export candidate series.

This script loads an existing evaluation report, decides whether any group
is MCPT-worthy, and exports candidate event return series CSVs.

Does NOT actually run MCPT. This is adapter/export plumbing only.

Usage:
    python -m venue_agnostic_signal_observer.run_mcpt_export \\
        --report-dir reports/derivatives_spot_lead_lag_v2_FAST_DIAGNOSTIC_... \\
        --out reports/mcpt_inputs \\
        --min-events 30 \\
        --max-groups 3 \\
        --cost-floor-bps 50
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .mcpt_export import (
    DEFAULT_COST_FLOOR_BPS,
    DEFAULT_MAX_GROUPS,
    DEFAULT_MIN_EVENTS,
    export_mcpt_candidate_series,
    export_mcpt_summary,
    select_mcpt_candidate_groups,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="MCPT export adapter: select candidate groups and export return series."
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
        help="Output directory for MCPT inputs. Defaults to <report-dir>/mcpt_inputs/.",
    )
    p.add_argument(
        "--min-events",
        type=int,
        default=DEFAULT_MIN_EVENTS,
        help=f"Minimum valid events for a group to be MCPT-worthy. Default: {DEFAULT_MIN_EVENTS}.",
    )
    p.add_argument(
        "--max-groups",
        type=int,
        default=DEFAULT_MAX_GROUPS,
        help=f"Maximum number of candidate groups to select. Default: {DEFAULT_MAX_GROUPS}.",
    )
    p.add_argument(
        "--cost-floor-bps",
        type=float,
        default=DEFAULT_COST_FLOOR_BPS,
        help=f"Assumed all-in cost floor in bps. Groups near this are skipped. Default: {DEFAULT_COST_FLOOR_BPS}.",
    )
    p.add_argument(
        "--report-slug",
        type=str,
        default="",
        help="Human-readable slug for output directory. Auto-detected from report-dir if omitted.",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    if not report_dir.exists():
        print(f"ERROR: report directory not found: {report_dir}", file=sys.stderr)
        sys.exit(1)

    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        print(f"ERROR: summary.json not found in {report_dir}", file=sys.stderr)
        sys.exit(1)

    with open(summary_path) as f:
        summary = json.load(f)

    all_groups = summary.get("results_by_group", [])
    if not all_groups:
        print("MCPT_SKIPPED: no results_by_group in summary")
        # Still write the summary
        out_dir = Path(args.out) if args.out else report_dir / "mcpt_inputs"
        export_mcpt_summary(
            report_dir=report_dir,
            selected_groups=[],
            all_groups=all_groups,
            skipped_reason="no_results_by_group",
            out_dir=out_dir,
        )
        return

    verdict = summary.get("verdict", "")
    capture_mode = summary.get("capture_mode", "")

    # Select candidate groups
    selected = select_mcpt_candidate_groups(
        all_groups,
        max_groups=args.max_groups,
        cost_floor_bps=args.cost_floor_bps,
        min_events=args.min_events,
    )

    if not selected:
        # Determine skip reason from the data
        all_net = [g.get("mean_net_bps") for g in all_groups if g.get("mean_net_bps") is not None]
        if all_net:
            from statistics import mean
            avg_net = mean(all_net)
            reason = (
                f"no_candidate_signal_to_falsify "
                f"(all {len(all_net)} groups have net returns around {avg_net:.1f} bps "
                f"— cost floor dust)"
            )
        else:
            reason = "no_candidate_signal_to_falsify (no finite net returns)"

        print(f"MCPT_SKIPPED: {reason}")

        out_dir = Path(args.out) if args.out else report_dir / "mcpt_inputs"
        export_mcpt_summary(
            report_dir=report_dir,
            selected_groups=[],
            all_groups=all_groups,
            skipped_reason=reason,
            out_dir=out_dir,
        )
        return

    # Export each selected group
    out_dir = Path(args.out) if args.out else report_dir / "mcpt_inputs"
    exported_paths: list[str] = []

    for group in selected:
        path = export_mcpt_candidate_series(
            report_dir=report_dir,
            candidate_group=group,
            out_dir=out_dir,
        )
        exported_paths.append(str(path))
        print(
            f"  EXPORTED: {path.name} "
            f"({group.get('signal_type', '?')}/{group.get('lookback_ms', '?')}ms/"
            f"{group.get('horizon_ms', '?')}ms, "
            f"net={group.get('mean_net_bps', '?'):.2f} bps, "
            f"n={group.get('valid_count', '?')})"
        )

    # Write summary
    summary_path = export_mcpt_summary(
        report_dir=report_dir,
        selected_groups=selected,
        all_groups=all_groups,
        skipped_reason=None,
        out_dir=out_dir,
    )

    print()
    print("=" * 60)
    print(f"MCPT EXPORT COMPLETE")
    print(f"  Report: {report_dir}")
    print(f"  Verdict: {verdict}")
    print(f"  Capture mode: {capture_mode or 'unspecified'}")
    print(f"  Groups with candidates: {len(selected)}/{len(all_groups)}")
    print(f"  Exported files: {len(exported_paths)}")
    print(f"  Output dir: {out_dir}")
    print(f"  Summary: {summary_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()