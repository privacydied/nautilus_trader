"""CLI runner for cross-capture consistency diagnostics.

Reads multiple existing derivatives spot lead-lag report directories and writes
JSON/CSV/Markdown aggregation by exact group config. Diagnostic-only: no live
capture, no registry updates, no candidate promotion.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from ._prog import set_legacy_prog

try:
    from venue_agnostic_signal_observer.cross_capture_consistency import (
        compute_cross_capture_consistency,
        write_cross_capture_consistency_reports,
    )
except ModuleNotFoundError:  # Support `python -m examples.strategies...` from repo root.
    from examples.strategies.venue_agnostic_signal_observer.cross_capture_consistency import (
        compute_cross_capture_consistency,
        write_cross_capture_consistency_reports,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-capture consistency aggregation for derivatives spot lead-lag reports.",
    )
    set_legacy_prog(parser, __name__)
    parser.add_argument(
        "--report-dirs",
        nargs="+",
        required=True,
        help="One or more evaluated report directories containing summary.json or summary.csv.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory for cross-capture consistency reports.",
    )
    parser.add_argument(
        "--min-captures",
        type=int,
        default=2,
        help="Minimum loaded report count needed for consistency verdict. Default: 2.",
    )
    parser.add_argument(
        "--merge-capture-modes",
        action="store_true",
        help="Do not include capture_mode in the group key. Default keeps capture_mode separate.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    report_dirs = [Path(p) for p in args.report_dirs]
    missing = [str(p) for p in report_dirs if not p.exists()]
    if missing:
        print("[ERROR] Missing report directories:")
        for p in missing:
            print(f"  - {p}")
        sys.exit(1)

    print("=" * 72)
    print("CROSS-CAPTURE CONSISTENCY DIAGNOSTIC")
    print("=" * 72)
    print(f"\n[1] Loading {len(report_dirs)} report directory/directories")
    for p in report_dirs:
        print(f"  - {p}")

    summary = compute_cross_capture_consistency(
        report_dirs=report_dirs,
        min_captures=args.min_captures,
        include_capture_mode=not args.merge_capture_modes,
    )

    print("\n[2] Aggregation summary")
    print(f"  Verdict: {summary.verdict}")
    print(f"  Reason: {summary.reason}")
    print(f"  Loaded reports: {summary.loaded_reports}/{summary.total_reports}")
    print(f"  Group observations: {summary.total_group_observations}")
    print(f"  Finite observations: {summary.finite_group_observations}")
    print(f"  Unique groups: {summary.unique_groups}")
    print(f"  Recurring groups: {summary.recurring_groups}")

    if summary.rows:
        print("\n  Top 5 groups by consistency:")
        for i, row in enumerate(summary.rows[:5], 1):
            print(
                f"    {i}. {row.get('source_venue')}→{row.get('target_venue')} "
                f"{row.get('symbol') or '(pooled)'} {row.get('signal_type')}/"
                f"{row.get('lookback_ms')}ms h={row.get('horizon_ms')}ms "
                f"captures={row.get('captures_seen')} +raw={row.get('positive_mean_raw_captures')} "
                f"median_raw={row.get('median_of_mean_raw_bps')} score={row.get('consistency_score')}"
            )

    print(f"\n[3] Writing reports to: {args.out}")
    write_cross_capture_consistency_reports(summary, args.out)
    print(f"\nVERDICT: {summary.verdict}")
    print(f"  Reports: {args.out}/")


if __name__ == "__main__":
    main()
