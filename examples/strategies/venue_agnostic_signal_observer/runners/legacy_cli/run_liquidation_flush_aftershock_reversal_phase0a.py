"""CLI runner for liquidation flush aftershock reversal v0 Phase 0A."""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    STATUS_INVALID_INPUT,
    STATUS_INVALID_PRECOMMITMENT,
    discover_archive_paths,
    run_from_archive_paths,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import create_run_id

DEFAULT_PRECOMMITMENT = Path(
    "examples/strategies/venue_agnostic_signal_observer/docs/"
    "LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_V0_PHASE0A_PRECOMMITMENT.md"
)
DEFAULT_OUT_ROOT = Path("reports/liquidation_flush_aftershock_reversal_phase0a")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive-only liquidation flush aftershock reversal Phase 0A audit")
    parser.add_argument("--archive-path", type=Path, action="append", help="Existing local archive file or directory. May be repeated.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_ROOT, help="Output root for timestamped run directory")
    parser.add_argument("--precommitment", type=Path, default=DEFAULT_PRECOMMITMENT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path.cwd()
    run_id = create_run_id("liquidation_flush_aftershock_reversal_phase0a")
    report_dir = (args.out / run_id).resolve()
    try:
        archive_paths = args.archive_path or discover_archive_paths(repo_root)
        if not archive_paths:
            print("PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE")
            print(f"report_path={report_dir}")
            print("archive_source_path=")
            print("archive_backfill_invoked=False")
            rows = []
            from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import LoadDiagnostics, run_phase0a_audit, write_report_artifacts
            result = run_phase0a_audit(rows, LoadDiagnostics(), args.precommitment, generated_at=datetime.now(UTC), repo_root=repo_root, archive_source_path="", archive_backfill_invoked=False)
            result.summary["status"] = "PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE"
            result.summary["unlocks_phase0b"] = False
            result.summary["phase0b_locked_reason"] = "no usable archive rows loaded from restricted discovery paths"
            write_report_artifacts(result, report_dir)
            return 0
        result = run_from_archive_paths(archive_paths, args.precommitment, repo_root, out_dir=report_dir, archive_backfill_invoked=False)
        summary = result.summary
        print(summary["status"])
        print(f"report_path={report_dir}")
        print(f"precommitment_sha256={summary.get('precommitment_sha256')}")
        print(f"archive_source_path={summary.get('archive_source_path')}")
        print(f"archive_end_age_days={summary.get('archive_end_age_days')}")
        print(f"archive_backfill_invoked={summary.get('archive_backfill_invoked')}")
        if summary["status"] in {STATUS_INVALID_INPUT, STATUS_INVALID_PRECOMMITMENT}:
            return 2
        return 0
    except Exception:
        report_dir.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (report_dir / "summary.md").write_text("# Phase 0A unhandled exception\n\n```\n" + tb + "\n```\n", encoding="utf-8")
        print(f"PHASE0A_UNHANDLED_EXCEPTION report_path={report_dir}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
