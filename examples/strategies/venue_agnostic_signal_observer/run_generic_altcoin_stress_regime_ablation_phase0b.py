"""CLI runner for generic altcoin stress regime ablation Phase 0B."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0b import (
    STATUS_ERROR_INVALID_PHASE0A,
    STATUS_ERROR_PRECOMMITMENT,
    run_phase0b,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import create_run_id

DEFAULT_PRECOMMITMENT = Path(
    "examples/strategies/venue_agnostic_signal_observer/"
    "docs/GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0B_PRECOMMITMENT.md",
)
DEFAULT_OUT_ROOT = Path(
    "reports/generic_altcoin_stress_regime_ablation_phase0b",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generic altcoin stress regime ablation Phase 0B return diagnostic",
    )
    parser.add_argument(
        "--phase0a-report",
        type=Path,
        required=True,
        help="Path to Phase 0A report directory containing summary.json",
    )
    parser.add_argument(
        "--archive-path",
        type=Path,
        action="append",
        required=True,
        help="Archive directory path (may be repeated)",
    )
    parser.add_argument(
        "--precommitment",
        type=Path,
        default=DEFAULT_PRECOMMITMENT,
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_ROOT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = create_run_id("generic_altcoin_stress_regime_ablation_phase0b")
    report_dir = (args.out / run_id).resolve()
    try:
        result = run_phase0b(
            args.phase0a_report,
            args.archive_path,
            args.precommitment,
            report_dir,
        )
        summary = result.summary
        print(summary.get("status"))
        print(f"report_path={report_dir}")
        print(f"precommitment_sha256={summary.get('precommitment_sha256')}")
        print(f"phase0a_event_count={summary.get('phase0a_event_count')}")
        print(f"phase0b_event_count_evaluated={summary.get('phase0b_event_count_evaluated')}")
        if summary.get("status") in {STATUS_ERROR_PRECOMMITMENT, STATUS_ERROR_INVALID_PHASE0A}:
            return 2
        return 0
    except Exception:
        report_dir.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (report_dir / "summary.md").write_text(
            "# Phase 0B unhandled exception\n\n```\n" + tb + "\n```\n",
            encoding="utf-8",
        )
        print(f"PHASE0B_ERROR report_path={report_dir}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())