"""CLI entrypoint for Phase 0C null/falsification validation."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_venue_age_aware_phase0c import (
    run_phase0c,
)

PHASE0C_PRECOMMITMENT = Path(
    "examples/strategies/venue_agnostic_signal_observer/docs/LIQUIDATION_FLUSH_AFTERSHOCK_REVERSAL_HYPERLIQUID_VENUE_AGE_AWARE_PHASE0C_NULL_PRECOMMITMENT.md"
)
REPORT_ROOT = Path("reports/liquidation_flush_aftershock_reversal_venue_age_aware_phase0c")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 0C null/falsification validation")
    parser.add_argument("--phase0a-report", required=True, type=Path)
    parser.add_argument("--phase0b-report", required=True, type=Path)
    parser.add_argument("--archive-path", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--cluster-iterations", type=int, default=1000)
    parser.add_argument("--profile-only", action="store_true")
    args = parser.parse_args()

    try:
        result = run_phase0c(
            phase0a_report_path=args.phase0a_report,
            phase0b_report_path=args.phase0b_report,
            archive_path=args.archive_path,
            precommitment_path=PHASE0C_PRECOMMITMENT,
            iterations=args.iterations,
            cluster_iterations=args.cluster_iterations,
            profile_only=args.profile_only,
            report_root=REPORT_ROOT,
        )
    except Exception:
        traceback.print_exc()
        raise

    print(json.dumps({"status": result.get("status"), "final_verdict": result.get("final_verdict"), "report_dir": result.get("report_dir")}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
