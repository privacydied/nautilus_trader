#!/usr/bin/env python3
"""CLI runner for Hyperliquid Supertrend 4h/1d Phase 0 scaffold."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import hyperliquid_supertrend_4h1d_phase0 as h


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Hyperliquid Supertrend 4h/1d Phase 0 observer-only feasibility scaffold")
    p.add_argument("--out", default="reports/hyperliquid_supertrend_4h1d_phase0")
    p.add_argument("--price-csv", default=None, help="Optional canonical hourly price CSV")
    p.add_argument("--funding-csv", default=None, help="Optional canonical funding CSV")
    p.add_argument("--precommitment", default="examples/strategies/venue_agnostic_signal_observer/docs/HYPERLIQUID_SUPERTREND_4H1D_PHASE0_PRECOMMITMENT.md")
    p.add_argument("--precommitment-hash", default="examples/strategies/venue_agnostic_signal_observer/docs/hyperliquid_supertrend_4h1d_phase0/precommitment_hash.txt")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = h.run_phase0(
        out_root=Path(args.out),
        precommitment_path=Path(args.precommitment),
        expected_hash_path=Path(args.precommitment_hash),
        price_csv=Path(args.price_csv) if args.price_csv else None,
        funding_csv=Path(args.funding_csv) if args.funding_csv else None,
        args=vars(args),
    )
    print(result["run_dir"])
    print(result["summary"]["overall_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
