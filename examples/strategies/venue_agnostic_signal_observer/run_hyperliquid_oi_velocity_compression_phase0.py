"""CLI runner for Hyperliquid OI velocity compression Phase 0 diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_oi_velocity_compression_phase0 import (
    Phase0Config,
    run_phase0_pipeline,
)

DEFAULT_DOC = Path(
    "examples/strategies/venue_agnostic_signal_observer/docs/"
    "HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_PRECOMMITMENT.md"
)
DEFAULT_HASH = Path(
    "examples/strategies/venue_agnostic_signal_observer/docs/precommitment_hash.txt"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observer-only Hyperliquid OI velocity compression Phase 0")
    parser.add_argument("--data-dir", type=Path, required=True, help="Local deterministic historical archive directory")
    parser.add_argument("--out", type=Path, default=Path("reports/hyperliquid_oi_velocity_compression_phase0"))
    parser.add_argument("--precommitment", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--precommitment-hash-file", type=Path, default=DEFAULT_HASH)
    parser.add_argument("--no-write-reports", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = Phase0Config()
    try:
        result = run_phase0_pipeline(
            args.data_dir,
            cfg,
            out_root=args.out,
            verify_hash=True,
            precommitment_path=args.precommitment,
            hash_path=args.precommitment_hash_file,
            write_reports=not args.no_write_reports,
            args={"data_dir": str(args.data_dir), "out": str(args.out)},
        )
    except RuntimeError as exc:
        if str(exc) in {"PRECOMMITMENT_HASH_MISMATCH", "PHASE0A_FUNDING_QUARANTINE_VIOLATION"}:
            print(json.dumps({"verdict": str(exc)}, sort_keys=True))
            return 2
        raise
    print(json.dumps({"verdict": result["summary"]["verdict"], "report_dir": result.get("report_dir")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
