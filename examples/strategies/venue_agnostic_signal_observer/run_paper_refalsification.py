"""CLI entrypoint for rolling paper strategy re-falsification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .paper.refalsification import (
    RefalsificationConfig,
    run_refalsification_once,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run paper strategy re-falsification pass"
    )
    parser.add_argument(
        "--registry-dir",
        type=str,
        required=True,
        help="Paper strategy registry directory",
    )
    parser.add_argument(
        "--ledger-path",
        type=str,
        required=True,
        help="Paper events ledger path",
    )
    parser.add_argument(
        "--pnl-ledger",
        type=str,
        required=True,
        help="PnL ledger path (for daily-loss-killed check)",
    )
    parser.add_argument(
        "--artifacts-root",
        type=str,
        required=True,
        help="Root directory for research artifacts (null, FDR, holdout, etc.)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run one refalsification pass and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report decisions without mutating registry or writing ledger",
    )
    args = parser.parse_args()

    config = RefalsificationConfig(
        registry_dir=Path(args.registry_dir),
        ledger_path=Path(args.ledger_path),
        pnl_ledger_path=Path(args.pnl_ledger),
        artifacts_root=Path(args.artifacts_root),
    )

    if args.once:
        decisions = run_refalsification_once(config, dry_run=args.dry_run)
        print(f"Refalsification pass complete: {len(decisions)} decision(s)")
        for d in decisions:
            status = "DISABLE" if d.should_disable else "KEEP"
            print(f"  {d.strategy_id}: {status} ({d.reason})")
    else:
        print("Run with --once for a single pass (loop mode not implemented in v0)")
        sys.exit(1)


if __name__ == "__main__":
    main()