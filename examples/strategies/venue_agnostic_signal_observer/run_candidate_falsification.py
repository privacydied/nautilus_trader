"""CLI runner for diagnostic candidate falsification summaries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


try:
    from venue_agnostic_signal_observer.candidate_falsification import (
        compute_candidate_falsification_summary,
    )
    from venue_agnostic_signal_observer.candidate_falsification import (
        write_candidate_falsification_reports,
    )
except ModuleNotFoundError:  # Support `python -m examples.strategies...` from repo root.
    from examples.strategies.venue_agnostic_signal_observer.candidate_falsification import (
        compute_candidate_falsification_summary,
    )
    from examples.strategies.venue_agnostic_signal_observer.candidate_falsification import (
        write_candidate_falsification_reports,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Combine existing diagnostic reports into a candidate falsification matrix.",
    )
    parser.add_argument("--evaluated-report-dir", help="Existing evaluated derivatives spot lead-lag report dir.")
    parser.add_argument("--cost-sensitivity-dir", help="Existing cost sensitivity report dir.")
    parser.add_argument("--permutation-null-dir", help="Existing permutation/null report dir.")
    parser.add_argument("--heatmap-dir", help="Existing lead/lag heatmap report dir.")
    parser.add_argument("--consistency-dir", help="Existing cross-capture consistency report dir.")
    parser.add_argument("--out", required=True, help="Output directory for falsification reports.")
    parser.add_argument("--viability-cost-bps", type=float, default=50.0, help="Cost threshold for cost-sensitivity viability. Default: 50.")
    parser.add_argument("--min-events", type=int, default=50, help="Minimum events for score penalty. Default: 50.")
    return parser


def _check_optional_dir(path: str | None, label: str) -> None:
    if path and not Path(path).exists():
        print(f"[ERROR] {label} not found: {path}")
        sys.exit(1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    for path, label in [
        (args.evaluated_report_dir, "evaluated report dir"),
        (args.cost_sensitivity_dir, "cost sensitivity dir"),
        (args.permutation_null_dir, "permutation/null dir"),
        (args.heatmap_dir, "heatmap dir"),
        (args.consistency_dir, "cross-capture consistency dir"),
    ]:
        _check_optional_dir(path, label)

    print("=" * 72)
    print("CANDIDATE FALSIFICATION SUMMARY")
    print("=" * 72)
    print("\n[1] Loading optional existing reports")
    summary = compute_candidate_falsification_summary(
        evaluated_report_dir=args.evaluated_report_dir,
        cost_sensitivity_dir=args.cost_sensitivity_dir,
        permutation_null_dir=args.permutation_null_dir,
        heatmap_dir=args.heatmap_dir,
        consistency_dir=args.consistency_dir,
        viability_cost_bps=args.viability_cost_bps,
        min_events=args.min_events,
    )

    print("\n[2] Falsification summary")
    print(f"  Verdict: {summary.verdict}")
    print(f"  Reason: {summary.reason}")
    print(f"  Total groups: {summary.total_groups}")
    print(f"  Surviving diagnostic groups: {summary.surviving_diagnostic_groups}")
    print(f"  Cost-wall blocked groups: {summary.cost_wall_blocked_groups}")
    if summary.missing_reports:
        print("  Missing report types:")
        for item in summary.missing_reports:
            print(f"    - {item}")

    if summary.rows:
        print("\n  Top 5 groups by evidence/survival sort:")
        for i, row in enumerate(summary.rows[:5], 1):
            print(
                f"    {i}. {row.get('source_venue')}→{row.get('target_venue')} "
                f"{row.get('symbol') or '(pooled)'} {row.get('signal_type')}/"
                f"{row.get('lookback_ms')}ms h={row.get('horizon_ms')}ms "
                f"score={row.get('falsification_score')} status={row.get('diagnostic_status')}"
            )

    print(f"\n[3] Writing reports to: {args.out}")
    write_candidate_falsification_reports(summary, args.out)
    print(f"\nVERDICT: {summary.verdict}")
    print(f"  Reports: {args.out}/")


if __name__ == "__main__":
    main()
