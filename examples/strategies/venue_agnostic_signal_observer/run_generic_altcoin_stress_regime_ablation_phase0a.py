"""CLI runner for generic altcoin stress regime ablation Phase 0A."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.generic_altcoin_stress_regime_ablation_phase0a import (
    STATUS_ARCHIVE_MISSING,
    STATUS_READY,
    _DEFAULT_WORKERS,
    discover_archive_paths,
    run_from_archive_paths,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import create_run_id

DEFAULT_PRECOMMITMENT = Path(
    "examples/strategies/venue_agnostic_signal_observer/"
    "docs/GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0_PRECOMMITMENT.md",
)
DEFAULT_OUT_ROOT = Path(
    "reports/generic_altcoin_stress_regime_ablation_phase0a",
)
REPO_ROOT = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generic altcoin stress regime ablation Phase 0A",
    )
    parser.add_argument(
        "--archive-path",
        type=Path,
        action="append",
        default=None,
        help="Archive path(s). Auto-discovered if not specified.",
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
    parser.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_WORKERS,
        help=f"Number of parallel workers (default: {_DEFAULT_WORKERS}, 1=single-process)",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Profile-only mode — max-symbols subset, non-validating",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=0,
        help="Max symbols to process (profile-only, non-validating)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = create_run_id("generic_altcoin_stress_regime_ablation_phase0a")
    report_dir = (args.out / run_id).resolve()
    try:
        archive_paths = args.archive_path or discover_archive_paths(REPO_ROOT)
        workers = max(args.workers, 1)

        result = run_from_archive_paths(
            archive_paths,
            args.precommitment,
            REPO_ROOT,
            out_dir=report_dir,
            workers=workers,
        )
        summary = result.summary
        print(summary.get("status"))
        print(f"report_path={report_dir}")
        print(f"precommitment_sha256={summary.get('precommitment_sha256')}")
        print(f"accepted_events_after_cooldown={summary.get('accepted_event_count_after_cooldown')}")
        print(f"symbols_with_3_events={summary.get('symbols_with_at_least_3_events')}")
        print(f"max_symbol_share={summary.get('max_symbol_event_share')}")
        if args.profile_only:
            print("PROFILE_ONLY_MODE — output is non-validating")
            return 0
        if summary.get("status") == STATUS_ARCHIVE_MISSING:
            return 1
        if summary.get("status") != STATUS_READY:
            return 2
        return 0
    except Exception:
        report_dir.mkdir(parents=True, exist_ok=True)
        tb = traceback.format_exc()
        (report_dir / "summary.md").write_text(
            "# Phase 0A unhandled exception\n\n```\n" + tb + "\n```\n",
            encoding="utf-8",
        )
        print(f"PHASE0A_ERROR report_path={report_dir}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())