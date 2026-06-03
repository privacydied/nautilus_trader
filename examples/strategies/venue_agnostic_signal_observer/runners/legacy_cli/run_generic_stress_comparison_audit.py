"""CLI runner for generic stress comparison audit.

No orders. No private keys. No trading auth. No live execution.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.generic_stress_comparison_audit import (
    run_comparison_audit,
    summary_markdown,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json,
    atomic_write_text,
    create_run_id,
)

DEFAULT_OUT_ROOT = Path("reports/generic_altcoin_stress_comparison_audit")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generic stress comparison audit",
    )
    parser.add_argument("--generic-phase0a-report", type=Path, required=True)
    parser.add_argument("--generic-phase0b-report", type=Path, required=True)
    parser.add_argument("--liquidation-phase0a-report", type=Path, default=None)
    parser.add_argument("--liquidation-phase0b-report", type=Path, default=None)
    parser.add_argument("--archive-path", type=Path, default=Path("data/hyperliquid_oi_velocity_compression_phase0"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--seed-boring", type=int, default=20260526)
    parser.add_argument("--seed-random", type=int, default=20260527)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = create_run_id("generic_altcoin_stress_comparison_audit")
    report_dir = (args.out / run_id).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_comparison_audit(
            generic_phase0a_report=args.generic_phase0a_report,
            generic_phase0b_report=args.generic_phase0b_report,
            liquidation_phase0a_report=args.liquidation_phase0a_report,
            liquidation_phase0b_report=args.liquidation_phase0b_report,
            archive_path=args.archive_path,
            boring_seed=args.seed_boring,
            random_seed=args.seed_random,
        )
    except Exception:
        tb = traceback.format_exc()
        error_path = report_dir / "error.log"
        error_path.write_text(tb, encoding="utf-8")
        print(f"AUDIT_ERROR report_path={report_dir}", file=sys.stderr)
        return 3

    # Write summary.json
    summary_json = {
        "study_id": "generic_altcoin_stress_comparison_audit",
        "stage": "full_audit",
        "safety_mode": "public_data_observer_only",
        "generic_phase0a_report": str(args.generic_phase0a_report),
        "generic_phase0b_report": str(args.generic_phase0b_report),
        "liquidation_phase0a_report": str(args.liquidation_phase0a_report) if args.liquidation_phase0a_report else "MISSING",
        "liquidation_phase0b_report": str(args.liquidation_phase0b_report) if args.liquidation_phase0b_report else "MISSING",
        "generic_artifact_hash": result.generic_artifact_hash,
        "generic_event_count": result.generic_event_count,
        "liquidation_artifacts_available": result.liquidation_artifacts_available,
        "liquidation_regenerated": result.liquidation_regenerated,
        "liquidation_benchmark_match": result.liquidation_benchmark_match,
        "exact_overlap_computed": result.exact_overlap_computed,
        "same_universe_comparison_status": result.same_universe_comparison_status,
        "independent_metrics": result.independent_metrics,
        "reported_generic_metrics": result.reported_generic_metrics,
        "independent_reproduces": result.independent_reproduces,
        "boring_control": result.boring_control,
        "random_control": result.random_control,
        "inverse_control": result.inverse_control,
        "boring_control_warning": result.boring_control_warning,
        "random_control_warning": result.random_control_warning,
        "inverse_control_positive": result.inverse_control_positive,
        "temporal_concentration": result.temporal_concentration,
        "year_concentration_exceeds_50pct": result.year_concentration_exceeds_50pct,
        "lookahead_verdict": result.lookahead_verdict,
        "percentile_verdict": result.percentile_verdict,
        "vol_window_verdict": result.vol_window_verdict,
        "entry_price_verdict": result.entry_price_verdict,
        "exit_price_verdict": result.exit_price_verdict,
        "cooldown_verdict": result.cooldown_verdict,
        "direction_verdict": result.direction_verdict,
        "contamination_verdict": result.contamination_verdict,
        "survivorship_verdict": result.survivorship_verdict,
        "final_status": result.final_status,
        "phase0c_recommended": result.phase0c_recommended,
        "phase0d_unlocked": result.phase0d_unlocked,
        "errors": result.errors,
    }
    atomic_write_json(report_dir / "summary.json", summary_json)

    # Write summary.md
    md = summary_markdown(result)
    atomic_write_text(report_dir / "summary.md", md)

    print(f"report_path={report_dir}")
    print(f"final_status={result.final_status}")
    print(f"phase0c_recommended={result.phase0c_recommended}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())