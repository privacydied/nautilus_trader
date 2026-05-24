from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hyperliquid_supertrend_v0_residual_diagnostic import most_recent_v0_report
from examples.strategies.venue_agnostic_signal_observer.hyperliquid_supertrend_v0_residual_diagnostic import run_diagnostic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnostic-only v0 1d residual analysis for Hyperliquid Supertrend artifacts")
    parser.add_argument("--v0-report-dir", type=Path, default=None)
    parser.add_argument("--reports-root", type=Path, default=Path("reports/hyperliquid_supertrend_4h1d_phase0"))
    parser.add_argument("--price-archive", type=Path, default=Path("data/hyperliquid_supertrend_4h1d_phase0/hourly_prices.csv"))
    parser.add_argument("--out-root", type=Path, default=Path("reports/hyperliquid_supertrend_v0_residual_diagnostic"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report_dir = args.v0_report_dir or most_recent_v0_report(args.reports_root)
    if report_dir is None or not report_dir.exists():
        print("MISSING_V0_REPORT_ARTIFACTS")
        return 1
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_root / ts
    summary = run_diagnostic(report_dir, out_dir, args.price_archive if args.price_archive.exists() else None)
    print(out_dir)
    print(",".join(summary["statuses"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
