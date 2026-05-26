"""CLI runner for liquidation vs generic stress ablation comparison."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.liquidation_vs_generic_stress_ablation_comparison import (
    run_diagnostic,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import create_run_id

DEFAULT_OUT_ROOT = Path(
    "reports/liquidation_vs_generic_stress_ablation_comparison",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Liquidation/OI flush vs generic price-only stress ablation comparison",
    )
    parser.add_argument("--liquidation-phase0a-report", type=Path, default=None)
    parser.add_argument("--liquidation-phase0b-report", type=Path, default=None)
    parser.add_argument("--generic-phase0a-report", type=Path, required=True)
    parser.add_argument("--generic-phase0b-report", type=Path, required=True)
    parser.add_argument("--archive-path", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = create_run_id("liquidation_vs_generic_stress_ablation_comparison")
    report_dir = (args.out / run_id).resolve()
    try:
        result = run_diagnostic(
            args.liquidation_phase0a_report,
            args.liquidation_phase0b_report,
            args.generic_phase0a_report,
            args.generic_phase0b_report,
            archive_path=args.archive_path,
            report_dir=report_dir,
        )
        print(f"report_path={report_dir}")
        print(f"verdict={result.get('final_verdict')}")
        return 0
    except Exception:
        report_dir.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (report_dir / "summary.md").write_text(
            "# Ablation comparison unhandled exception\n\n```\n" + tb + "\n```\n",
            encoding="utf-8",
        )
        print(f"COMPARISON_ERROR report_path={report_dir}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())